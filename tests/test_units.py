# -*- coding: utf-8 -*-
"""Pure units: framing, tokens and proofs, the file service, the record store."""

import base64
import hashlib
import json
import os
import shutil
import tempfile
import time
import unittest

from agentipc import auth, crypto, files, proto
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
                "id": "abc123", "name": "blob.dat", "index": index, "total": len(chunks),
                "size": len(data), "sha256": digest,
                "data": base64.b64encode(chunks[index]).decode("ascii")}}
            service._h_file(pkt)
        with open(os.path.join(self.dir, "SENDER", "blob.dat"), "rb") as fh:
            self.assertEqual(fh.read(), data)

    def test_wrong_checksum_is_discarded(self):
        node = self._node()
        service = files.FileService(node, self.dir)
        service._h_file({"src": "S", "payload": {
            "id": "t1", "name": "x.txt", "index": 0, "total": 1, "size": 3,
            "sha256": "0" * 64, "data": base64.b64encode(b"abc").decode("ascii")}})
        self.assertFalse(os.path.exists(os.path.join(self.dir, "S", "x.txt")))

    def test_bad_names_and_base64_are_ignored(self):
        node = self._node()
        service = files.FileService(node, self.dir)
        service._h_file({"src": "S", "payload": {
            "id": "t2", "name": "../../etc", "index": 0, "total": 1, "size": 1,
            "sha256": "0" * 64, "data": "AAAA"}})
        service._h_file({"src": "S", "payload": {
            "id": "t3", "name": "ok.bin", "index": 0, "total": 1, "size": 1,
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
