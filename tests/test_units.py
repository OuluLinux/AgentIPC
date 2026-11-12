# -*- coding: utf-8 -*-
"""Pure units: framing, tokens and proofs, the file service, the record store."""

import base64
import hashlib
import json
import os
import shutil
import socket
import stat
import tempfile
import threading
import time
import unittest

from agentipc import auth, cli, crypto, daemon, files, linkcrypt, proto
from agentipc.node import Node
from agentipc.routing import SeenCache, Topology
from agentipc.vserver import RecordStore, valid_record


class ProtoTest(unittest.TestCase):
    def test_hello_round_trip(self):
        frame = proto.decode(proto.encode(proto.hello("node-1", "ssh")))
        self.assertEqual(frame["node"], "node-1")

    def test_packet_round_trip(self):
        frame = proto.decode(proto.encode(proto.packet("A", "*", "ping", {"t": 1.5})))
        self.assertEqual(frame["kind"], "ping")
        self.assertEqual(frame["dst"], "*")

    def test_malformed_frames_are_rejected(self):
        bad = [b"not json\n", b"[]\n", b'{"type": "nope"}\n', b'{"type": "hello", "node": "bad node!"}\n',
               b'{"type": "packet", "src": "A", "dst": "B", "id": "1", "ttl": 0, "kind": "x", "payload": {}}\n',
               b'{"type": "packet", "src": "A", "dst": "B", "id": "1", "ttl": 3, "kind": "x", "payload": []}\n']
        for line in bad:
            with self.assertRaises(proto.ProtoError):
                proto.decode(line)

    def test_multicast_destinations_are_validated(self):
        proto.decode(proto.encode(proto.packet("A", ["B", "C"], "x", {})))
        proto.decode(proto.encode(proto.packet("A", ["B"], "x", {})))
        bad = [[], ["B", "B"], ["B", "*"], ["B", 3], ["B bad"], ["x"] * 65]
        for dst in bad:
            with self.assertRaises(proto.ProtoError):
                proto.decode(proto.encode(proto.packet("A", dst, "x", {})))

    def test_oversized_line_is_rejected(self):
        with self.assertRaises(proto.ProtoError):
            proto.decode(b"{" + b"x" * (proto.MAX_LINE + 1))

    def test_ids_are_16_hex_characters_and_unique(self):
        ids = set(proto.new_id() for _ in range(1000))
        self.assertEqual(len(ids), 1000)
        self.assertTrue(all(len(i) == 16 for i in ids))


class TopologyTest(unittest.TestCase):
    def test_next_hop_follows_shortest_path(self):
        t = Topology("A")
        t.set_own(["B"])
        t.update("B", 1, ["A", "C"], time.time())
        t.update("C", 1, ["B"], time.time())
        self.assertEqual(t.next_hop("C"), "B")

    def test_stale_announcement_is_ignored(self):
        t = Topology("A")
        t.update("B", 5, ["A"], time.time())
        self.assertFalse(t.update("B", 4, ["A", "C"], time.time()))
        self.assertIsNone(t.next_hop("C"))

    def test_restarted_node_announces_outrank_its_old_ones(self):
        old = Topology("A")
        for _ in range(3):
            old_seq = old.set_own(["B"])
        time.sleep(0.01)
        restarted = Topology("A")
        self.assertGreater(restarted.set_own(["B"]), old_seq)

    def test_one_sided_claim_is_not_a_route(self):
        t = Topology("A")
        t.set_own(["B"])
        t.update("B", 1, ["A"], time.time())
        t.update("C", 1, ["B"], time.time())   # C says it links to B; B does not say so back
        self.assertIsNone(t.next_hop("C"))
        t.update("B", 2, ["A", "C"], time.time())
        self.assertEqual(t.next_hop("C"), "B")

    def test_expired_origin_is_forgotten(self):
        t = Topology("A")
        t.set_own(["B"])
        t.update("B", 1, ["A", "C"], time.time() - 100)
        t.update("C", 1, ["B"], time.time() - 100)
        self.assertEqual(sorted(t.expire(time.time(), 10)), ["B", "C"])
        self.assertIsNone(t.next_hop("C"))


class SeenCacheTest(unittest.TestCase):
    def test_duplicates_are_detected_and_old_entries_evicted(self):
        cache = SeenCache(size=2)
        self.assertTrue(cache.add(("A", "1")))
        self.assertFalse(cache.add(("A", "1")))
        cache.add(("A", "2"))
        cache.add(("A", "3"))
        self.assertTrue(cache.add(("A", "1")))


class AuthTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.accounts = auth.Accounts(os.path.join(self.dir, "accounts.json"))
        self.accounts.add_user("alice", "pw")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_proof_matches_only_with_the_right_password(self):
        rec = self.accounts.user("alice")
        salt, key = auth.unhexlify(rec["salt"]), auth.unhexlify(rec["key"])
        good = auth.proof_for(auth.derive_key("pw", salt, rec["iters"]), "nonce-1")
        self.assertEqual(good, auth.proof_for(key, "nonce-1"))
        self.assertNotEqual(good, auth.proof_for(auth.derive_key("wrong", salt, rec["iters"]), "nonce-1"))
        self.assertNotEqual(good, auth.proof_for(key, "nonce-2"))

    def test_token_round_trip_and_tamper_detection(self):
        expires = int(time.time()) + 60
        token = auth.make_token(self.accounts, "alice", "plain", expires)
        info = auth.parse_token(self.accounts, token)
        self.assertEqual(info["user"], "alice")
        self.assertEqual(info["server"], "plain")
        body, mac = token.split(".")
        self.assertIsNone(auth.parse_token(self.accounts, body + "." + ("0" * len(mac))))
        self.assertIsNone(auth.parse_token(self.accounts, "zz.zz"))

    def test_pbkdf2_fallback_gives_the_same_key_as_hashlib(self):
        if not hasattr(hashlib, "pbkdf2_hmac"):
            self.skipTest("hashlib.pbkdf2_hmac needs Python 2.7.8 or later")
        expected = hashlib.pbkdf2_hmac("sha256", b"pw", b"salt-salt-salt-x", 50, 32)
        self.assertEqual(auth.pbkdf2_sha256(b"pw", b"salt-salt-salt-x", 50), expected)

    def test_same_compares_wire_strings_and_refuses_mixed_or_non_ascii(self):
        self.assertTrue(auth.same("abc", u"abc"))
        self.assertFalse(auth.same("abc", "abd"))
        self.assertFalse(auth.same("abc", "abcd"))
        self.assertFalse(auth.same("abc", u"ab\xe9"))

    def test_expired_token_is_refused(self):
        token = auth.make_token(self.accounts, "alice", "plain", int(time.time()) - 1)
        self.assertIsNone(auth.parse_token(self.accounts, token))

    def test_unknown_user_gets_a_stable_fake_salt(self):
        self.assertEqual(self.accounts.fake_salt("nobody"), self.accounts.fake_salt("nobody"))
        self.assertNotEqual(self.accounts.fake_salt("nobody"), self.accounts.fake_salt("alice"))

    def test_accounts_file_is_private(self):
        mode = os.stat(os.path.join(self.dir, "accounts.json")).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_accounts_reload_when_another_process_changes_them(self):
        other = auth.Accounts(os.path.join(self.dir, "accounts.json"))
        other.add_user("bob", "pw2")
        time.sleep(0.01)
        other.add_user("carol", "pw3")
        self.assertIsNotNone(self.accounts.user("carol"))


class CryptoTest(unittest.TestCase):
    def test_round_trip_and_wrong_key(self):
        try:
            key = crypto.new_key()
        except crypto.CryptoError:
            self.skipTest("PyNaCl not installed")
        text = crypto.encrypt(key, "secret words")
        self.assertEqual(crypto.decrypt(key, text), b"secret words")
        with self.assertRaises(crypto.CryptoError):
            crypto.decrypt(crypto.new_key(), text)


class FileServiceTest(unittest.TestCase):
    """Chunks may arrive out of order; the file is checked and moved into place."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def _node(self):
        from agentipc.node import Node
        return Node("RECV")

    def test_out_of_order_chunks_assemble_and_verify(self):
        node = self._node()
        service = files.FileService(node, self.dir)
        data = os.urandom(files.CHUNK_SIZE * 2 + 17)
        digest = hashlib.sha256(data).hexdigest()
        chunks = [data[i:i + files.CHUNK_SIZE] for i in range(0, len(data), files.CHUNK_SIZE)]
        order = [2, 0, 1]
        for index in order:
            pkt = {"src": "SENDER", "payload": {
                "user": "alice", "id": "abc123", "name": "blob.dat", "index": index, "total": len(chunks),
                "size": len(data), "sha256": digest,
                "data": base64.b64encode(chunks[index]).decode("ascii")}}
            service._h_file(pkt)
        with open(os.path.join(self.dir, "alice@SENDER", "blob.dat"), "rb") as fh:
            self.assertEqual(fh.read(), data)

    def test_wrong_checksum_is_discarded(self):
        node = self._node()
        service = files.FileService(node, self.dir)
        service._h_file({"src": "S", "payload": {
            "user": "alice", "id": "t1", "name": "x.txt", "index": 0, "total": 1, "size": 3,
            "sha256": "0" * 64, "data": base64.b64encode(b"abc").decode("ascii")}})
        self.assertFalse(os.path.exists(os.path.join(self.dir, "alice@S", "x.txt")))

    def test_bad_names_and_base64_are_ignored(self):
        node = self._node()
        service = files.FileService(node, self.dir)
        service._h_file({"src": "S", "payload": {
            "user": "alice", "id": "t2", "name": "../../etc", "index": 0, "total": 1, "size": 1,
            "sha256": "0" * 64, "data": "AAAA"}})
        service._h_file({"src": "S", "payload": {
            "user": "alice", "id": "t3", "name": "ok.bin", "index": 0, "total": 1, "size": 1,
            "sha256": "0" * 64, "data": "!!!not base64"}})
        self.assertEqual(service.partial, {})


class ShellTest(unittest.TestCase):
    def test_output_and_exit_code(self):
        from agentipc import shell
        result = shell.run_command("echo ok; echo bad >&2; exit 4", 10)
        self.assertEqual(result["stdout"], "ok\n")
        self.assertEqual(result["stderr"], "bad\n")
        self.assertEqual(result["exit"], 4)
        self.assertFalse(result["timed_out"])

    def test_timeout_kills_the_command(self):
        from agentipc import shell
        result = shell.run_command("sleep 30", 0.5)
        self.assertTrue(result["timed_out"])
        self.assertIsNone(result["exit"])

    def test_large_output_is_cut(self):
        from agentipc import shell
        result = shell.run_command("head -c 200000 /dev/zero | tr '\\0' a", 10)
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["stdout"]), shell.MAX_OUTPUT)


class RecordTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "records.jsonl")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def rec(self, origin, oseq, body="x"):
        return {"origin": origin, "oseq": oseq, "server": "plain", "chan": "general",
                "user": "alice", "body": body, "ts": "2026-01-01T00:00:%02dZ" % oseq}

    def test_add_is_idempotent_and_survives_reload(self):
        store = RecordStore(self.path)
        self.assertTrue(store.add(self.rec("A", 1)))
        self.assertFalse(store.add(self.rec("A", 1)))
        again = RecordStore(self.path)
        self.assertEqual(len(again.records), 1)

    def test_after_returns_only_what_the_peer_lacks(self):
        store = RecordStore(None)
        store.add(self.rec("A", 1))
        store.add(self.rec("A", 2))
        store.add(self.rec("B", 1))
        missing = store.after({"A": 1})
        self.assertEqual(sorted((r["origin"], r["oseq"]) for r in missing), [("A", 2), ("B", 1)])

    def test_invalid_records_are_refused(self):
        self.assertTrue(valid_record(self.rec("A", 1)))
        bad = self.rec("A", 1)
        bad["oseq"] = "one"
        self.assertFalse(valid_record(bad))
        bad = self.rec("A", 1)
        bad["origin"] = "not valid!"
        self.assertFalse(valid_record(bad))


if __name__ == "__main__":
    unittest.main()


class ExplainTest(unittest.TestCase):
    def test_known_codes_get_a_reason_and_keep_the_code(self):
        from agentipc import client
        self.assertEqual(client.explain("ERR bad-login"), "ERR bad-login: wrong user or password")

    def test_unknown_codes_pass_through(self):
        from agentipc import client
        self.assertEqual(client.explain("ERR something-new"), "ERR something-new")


class VersionTest(unittest.TestCase):
    def test_hello_carries_versions_and_features(self):
        frame = proto.hello("node-1", "ssh")
        self.assertEqual(frame["version"], proto.PROTOCOL_VERSION)
        self.assertEqual(frame["min_version"], proto.MIN_PROTOCOL_VERSION)
        self.assertIn("core", frame["features"])
        self.assertEqual(proto.decode(proto.encode(frame))["features"], frame["features"])

    def test_negotiate_picks_the_highest_common_version(self):
        self.assertEqual(proto.negotiate(proto.PROTOCOL_VERSION, proto.MIN_PROTOCOL_VERSION),
                         proto.PROTOCOL_VERSION)
        self.assertEqual(proto.negotiate(proto.PROTOCOL_VERSION + 3, proto.MIN_PROTOCOL_VERSION),
                         proto.PROTOCOL_VERSION)

    def test_negotiate_refuses_non_overlapping_ranges(self):
        self.assertIsNone(proto.negotiate(proto.MIN_PROTOCOL_VERSION - 1, 1))
        self.assertIsNone(proto.negotiate(proto.PROTOCOL_VERSION + 1, proto.PROTOCOL_VERSION + 1))

    def test_bad_version_fields_are_rejected(self):
        frame = proto.hello("node-1", "ssh")
        for patch in ({"version": "2"}, {"min_version": "x"}, {"features": "core"}, {"features": [1]}):
            bad = dict(frame, **patch)
            with self.assertRaises(proto.ProtoError):
                proto.decode(proto.encode(bad))


class FeatureGateTest(unittest.TestCase):
    def test_kinds_need_the_matching_feature(self):
        self.assertEqual(proto.needs_feature("exec"), "shell")
        self.assertEqual(proto.needs_feature("file"), "file")
        self.assertEqual(proto.needs_feature("login-proof"), "vs")
        self.assertEqual(proto.needs_feature("ping"), "core")

    def test_send_skips_a_node_that_lacks_the_feature(self):
        from agentipc.node import Node
        node = Node("A")
        node.features["B"] = ["core"]
        self.assertFalse(node._takes("B", "exec"))
        self.assertTrue(node._takes("B", "ping"))
        self.assertFalse(node.send("B", "exec", {}))

    def test_nodes_that_announce_nothing_are_not_refused(self):
        from agentipc.node import Node
        node = Node("A")
        self.assertTrue(node._takes("B", "exec"))


class RecordNumberTest(unittest.TestCase):
    def test_restarted_node_does_not_reuse_record_numbers(self):
        from agentipc.node import Node
        before = Node("A")
        last = [before.next_oseq() for _ in range(3)][-1]
        time.sleep(0.01)
        after = Node("A")
        self.assertGreater(after.next_oseq(), last)


class RetentionTest(unittest.TestCase):
    def rec(self, oseq, ts):
        return {"origin": "A", "oseq": oseq, "server": "s", "chan": "general", "user": "u",
                "body": "x", "ts": ts}

    def test_old_records_are_refused_and_pruned(self):
        from agentipc import vserver
        now = time.time()
        path = os.path.join(tempfile.mkdtemp(), "records.jsonl")
        store = vserver.RecordStore(path, keep_days=7)
        old = vserver.cutoff_for(30, now)
        fresh = vserver.cutoff_for(1, now)
        self.assertFalse(store.add(self.rec(1, old)))
        self.assertTrue(store.add(self.rec(2, fresh)))
        # Stored before retention was set: prune drops it, and the file is rewritten.
        store.keep_days = None
        store.add(self.rec(3, old))
        store.keep_days = 7
        self.assertEqual(store.prune(now), 1)
        self.assertEqual([r["oseq"] for r in store.records], [2])
        reloaded = vserver.RecordStore(path, keep_days=7)
        self.assertEqual([r["oseq"] for r in reloaded.records], [2])

    def test_retention_off_keeps_everything(self):
        from agentipc import vserver
        store = vserver.RecordStore(None)
        self.assertTrue(store.add(self.rec(1, "2001-01-01T00:00:00Z")))
        self.assertEqual(store.prune(), 0)

    def test_cutoff_format_matches_record_timestamps(self):
        from agentipc import vserver
        self.assertEqual(vserver.cutoff_for(1, 0), "1969-12-31T00:00:00Z")


class FakeCtx(object):
    """The CLI's context: a home directory and the config."""

    def __init__(self, home, cfg):
        self.home = home
        self.cfg = cfg


class FakeLink(object):
    """Stands in for a link: records what is sent over it."""

    def __init__(self, peer, kind="ssh"):
        self.peer = peer
        self.kind = kind
        self.closed = False
        self.sent = []

    def send(self, pkt):
        self.sent.append(pkt)


class FailoverTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.accounts_path = os.path.join(self.dir, "accounts.json")
        self.replica_path = os.path.join(self.dir, auth.REPLICA_FILE)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def replica_frame(self, src, rev, epoch=0, authority=None, data=None):
        return {"src": src, "kind": "replica", "dst": "mg", "payload": {
            "authority": authority or src, "epoch": epoch, "rev": rev,
            "data": data or {"secret": "00", "users": {}, "servers": {}}}}

    def test_a_higher_epoch_claim_replaces_ours_and_lower_or_equal_do_not(self):
        node = Node("mg", authority="gen7")
        seen = []
        node.on_claim = lambda name, epoch: seen.append((name, epoch))
        node._take_claim({"node": "mg", "epoch": 1})
        self.assertEqual((node.authority, node.epoch), ("mg", 1))
        node._take_claim({"node": "gen7", "epoch": 1})
        node._take_claim({"node": "gen7", "epoch": 0})
        self.assertEqual((node.authority, node.epoch), ("mg", 1))
        self.assertEqual(seen, [("mg", 1)])

    def test_bad_claims_are_ignored(self):
        node = Node("mg", authority="gen7")
        for claim in (None, "gen7", {"node": "bad name", "epoch": 5}, {"node": "x", "epoch": True},
                      {"node": "x", "epoch": "2"}):
            node._take_claim(claim)
        self.assertEqual((node.authority, node.epoch), ("gen7", 0))

    def test_the_accounts_go_only_over_ssh_and_only_when_they_change(self):
        accounts = auth.Accounts(self.accounts_path)
        accounts.add_user("alice", "pw")
        node = Node("gen7", authority="gen7", standby="mg")
        authority = auth.install(node, accounts, self.replica_path)
        direct = FakeLink("mg", kind="direct")
        node.links.append(direct)
        authority.replicate_once()
        self.assertEqual(direct.sent, [])
        ssh = FakeLink("mg")
        node.links.append(ssh)
        authority.replicate_once()
        self.assertEqual([p["kind"] for p in ssh.sent], ["replica"])
        self.assertEqual(ssh.sent[0]["ttl"], 1)
        self.assertEqual(ssh.sent[0]["dst"], "mg")
        authority.replicate_once()
        self.assertEqual(len(ssh.sent), 1)
        accounts.add_user("bob", "pw2")
        authority.replicate_once()
        self.assertEqual(len(ssh.sent), 2)
        self.assertIn("bob", ssh.sent[1]["payload"]["data"]["users"])

    def test_a_standby_keeps_only_newer_replicas_from_its_ssh_link(self):
        node = Node("mg", authority="gen7")
        authority = auth.install(node, None, self.replica_path)
        authority._h_replica(self.replica_frame("gen7", 3), FakeLink("gen7", kind="direct"))
        self.assertIsNone(auth.read_replica(self.replica_path))
        authority._h_replica(self.replica_frame("mg", 3), FakeLink("mg"))
        self.assertIsNone(auth.read_replica(self.replica_path))
        authority._h_replica(self.replica_frame("gen7", 3), FakeLink("gen7"))
        self.assertEqual(auth.read_replica(self.replica_path)["rev"], 3)
        self.assertEqual(stat.S_IMODE(os.stat(self.replica_path).st_mode), 0o600)
        authority._h_replica(self.replica_frame("gen7", 2), FakeLink("gen7"))
        self.assertEqual(auth.read_replica(self.replica_path)["rev"], 3)
        authority._h_replica(self.replica_frame("gen7", 4, epoch=0), FakeLink("gen7"))
        self.assertEqual(auth.read_replica(self.replica_path)["rev"], 4)

    def test_promote_takes_the_replica_with_the_next_epoch(self):
        accounts = auth.Accounts(self.accounts_path)
        accounts.add_user("alice", "pw")
        accounts.data["authority"] = "gen7"
        accounts.save()
        auth.write_private(self.replica_path, {"authority": "gen7", "epoch": 3, "rev": 9,
                                               "data": dict(accounts.data, epoch=3)})
        self.assertEqual(auth.promote(self.replica_path, self.accounts_path, "mg"), 4)
        promoted = auth.Accounts(self.accounts_path).data
        self.assertEqual((promoted["authority"], promoted["epoch"]), ("mg", 4))
        self.assertIsNotNone(auth.Accounts(self.accounts_path).user("alice"))
        with self.assertRaises(ValueError):
            auth.promote(self.replica_path, self.accounts_path, "mg")

    def test_promote_needs_a_replica(self):
        with self.assertRaises(ValueError):
            auth.promote(self.replica_path, self.accounts_path, "mg")

    def test_a_node_without_accounts_refuses_login_and_verify(self):
        node = Node("mg", authority="gen7")
        authority = auth.install(node, None, self.replica_path)
        sent = []
        authority._reply = lambda dst, kind, req, body: sent.append((kind, dict(body, req=req)))
        authority._h_verify({"src": "td", "payload": {"req": "r1", "token": "x"}})
        authority._h_login_start({"src": "td", "payload": {"req": "r2", "user": "alice", "server": "plain"}})
        self.assertEqual(sent, [("verify-result", {"ok": False, "req": "r1"}),
                                ("login-result", {"ok": False, "reason": "not-authority", "req": "r2"})])

    def test_account_writes_are_refused_when_another_node_is_the_authority(self):
        accounts = auth.Accounts(self.accounts_path)
        accounts.data["authority"] = "mg"
        accounts.save()
        ctx = FakeCtx(home=self.dir, cfg={"node": "gen7", "authority": "gen7"})
        self.assertEqual(cli._accounts(ctx).data["authority"], "mg")
        with self.assertRaises(cli.StoreError):
            cli._accounts(ctx, write=True)


class LinkCryptTest(unittest.TestCase):
    """The encrypted direct-link handshake, over a socket pair with the same file wrappers as daemon."""

    def setUp(self):
        try:
            self.dir = tempfile.mkdtemp()
            self.alice = linkcrypt.identity(os.path.join(self.dir, "alice"))
            self.bob = linkcrypt.identity(os.path.join(self.dir, "bob"))
            self.mallory = linkcrypt.identity(os.path.join(self.dir, "mallory"))
        except linkcrypt.LinkCryptoError:
            self.skipTest("PyNaCl not installed")
        self.alice_pub = linkcrypt.public_key_hex(self.alice)
        self.bob_pub = linkcrypt.public_key_hex(self.bob)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def pair(self, dial_pubkey=None, trusted=None):
        """Runs dial (as alice) against accept (as bob) and returns both results or errors."""
        left, right = socket.socketpair()
        lr, lw = left.makefile("rb"), left.makefile("wb")
        rr, rw = right.makefile("rb"), right.makefile("wb")
        out = {}

        def responder():
            try:
                out["bob"] = linkcrypt.accept(rr, rw, "bob", self.bob,
                                              trusted if trusted is not None else {"alice": self.alice_pub})
            except Exception as exc:
                out["bob"] = exc
                right.shutdown(socket.SHUT_RDWR)

        thread = threading.Thread(target=responder)
        thread.start()
        try:
            out["alice"] = linkcrypt.dial(lr, lw, "alice", self.alice,
                                          dial_pubkey if dial_pubkey is not None else self.bob_pub)
        except Exception as exc:
            out["alice"] = exc
        thread.join(10)
        for sock in (left, right):
            sock.close()
        return out

    def test_handshake_and_sealed_lines(self):
        out = self.pair()
        self.assertNotIsInstance(out["alice"], Exception)
        self.assertNotIsInstance(out["bob"], Exception)
        alice_streams, name = out["alice"]
        bob_streams, peer = out["bob"]
        self.assertEqual(name, "bob")
        self.assertEqual(peer, "alice")
        alice_streams.write(b'{"type": "hello"}\n')
        alice_streams.flush()
        self.assertEqual(bob_streams.readline(proto.MAX_LINE + 1), b'{"type": "hello"}\n')

    def test_wrong_peer_key_is_refused(self):
        out = self.pair(dial_pubkey=linkcrypt.public_key_hex(self.mallory))
        self.assertIsInstance(out["alice"], linkcrypt.LinkCryptoError)

    def test_untrusted_dialer_is_refused(self):
        out = self.pair(trusted={"carol": self.alice_pub})
        self.assertIsInstance(out["bob"], linkcrypt.LinkCryptoError)

    def test_tampered_and_plain_lines_do_not_open(self):
        out = self.pair()
        alice_streams, _ = out["alice"]
        bob_streams, _ = out["bob"]
        alice_streams.write(b"real\n")
        alice_streams.flush()
        line = bob_streams.rfile.readline(linkcrypt.SEALED_LIMIT)
        sealed = json.loads(line.decode("ascii"))
        flipped = base64.b64decode(sealed["enc"].encode("ascii"))
        flipped = flipped[:-1] + bytes(bytearray([flipped[-1] ^ 1]))
        bad = (json.dumps({"enc": base64.b64encode(flipped).decode("ascii")}) + "\n").encode("ascii")
        for raw in (bad, b'{"type": "hello"}\n'):
            left, right = socket.socketpair()
            try:
                left.sendall(raw)
                left.shutdown(socket.SHUT_WR)
                reader = linkcrypt.Sealed(right.makefile("rb"), right.makefile("wb"), bytes(bytearray(32)))
                with self.assertRaises(linkcrypt.LinkCryptoError):
                    reader.readline(proto.MAX_LINE + 1)
            finally:
                left.close()
                right.close()

    def test_identity_is_stable_and_private(self):
        home = os.path.join(self.dir, "stable")
        first = linkcrypt.identity(home)
        again = linkcrypt.identity(home)
        self.assertEqual(linkcrypt.public_key_hex(first), linkcrypt.public_key_hex(again))
        mode = stat.S_IMODE(os.stat(os.path.join(home, linkcrypt.IDENTITY_FILE)).st_mode)
        self.assertEqual(mode, 0o600)


class StatusTest(unittest.TestCase):
    """node status reports the live claim and whether the accounts file agrees with it."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.accounts = os.path.join(self.dir, "accounts.json")
        with open(self.accounts, "w") as fh:
            json.dump({"authority": "gen7", "epoch": 4, "rev": 4, "users": {}, "servers": {}}, fh)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_matching_file_and_live_claim(self):
        d = daemon.Daemon("gen7", {"node": "gen7"}, self.dir)
        st = d.status()
        self.assertEqual((st["authority"], st["epoch"], st["is_authority"]), ("gen7", 4, True))
        self.assertTrue(st["file_matches_claim"])
        self.assertEqual(st["links"], [])

    def test_file_changed_after_start_is_flagged(self):
        d = daemon.Daemon("gen7", {"node": "gen7"}, self.dir)
        with open(self.accounts, "w") as fh:
            json.dump({"authority": "mg", "epoch": 5, "rev": 5, "users": {}, "servers": {}}, fh)
        st = d.status()
        self.assertEqual(st["epoch"], 4)
        self.assertFalse(st["file_matches_claim"])
