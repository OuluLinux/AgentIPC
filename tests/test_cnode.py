"""The C node's handshake, checked against frames from the Python protocol module."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO))

from agentipc import proto  # noqa: E402
from mesh_util import communicate  # noqa: E402

CNODE_DIR = os.path.join(REPO, "cnode")
AGENT_PYTHON = os.environ.get("AGENTIPC_PYTHON", sys.executable)
BINARY = os.path.join(CNODE_DIR, "cnode")


def _which(name):
    """shutil.which, for Python 2.7 too."""
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        path = os.path.join(directory, name)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def run_cnode(lines, node="cnode-1", extra=()):
    """Feeds frame lines to the C node on stdin. Returns (exit code, frames out, stderr)."""
    data = "".join(lines).encode("ascii")
    p = subprocess.Popen([BINARY, "--stdio", "--node", node] + list(extra), stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = communicate(p, 10, data)
    frames = [json.loads(line) for line in out.decode("ascii").splitlines() if line]
    return p.returncode, frames, err.decode("ascii")


def packet(dst, kind="ping", payload=None, src="py-peer", **changes):
    """A packet line built by the Python module, with fields overridden."""
    f = proto.packet(src, dst, kind, {"t": 12.5} if payload is None else payload)
    f.update(changes)
    return json.dumps(f) + "\n"


def frame(**changes):
    """A hello frame built by the Python module, with fields overridden."""
    f = proto.hello("py-peer", "ssh")
    f.update(changes)
    return json.dumps(f) + "\n"


@unittest.skipUnless(_which("make") and (_which("cc") or _which("gcc")),
                     "needs make and a C compiler")
class HandshakeTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        subprocess.check_call(["make", "-s", "-C", CNODE_DIR, "cnode"])

    def test_python_hello_is_accepted_and_c_hello_is_valid_python(self):
        code, frames, err = run_cnode([frame()])
        self.assertEqual(code, 0, err)
        self.assertIn("linked to py-peer at version 2", err)
        # The C node's own hello must decode with the Python module.
        decoded = proto.decode((json.dumps(frames[0]) + "\n").encode("ascii"))
        self.assertEqual(decoded["node"], "cnode-1")
        self.assertEqual(decoded["version"], 2)
        self.assertEqual(decoded["min_version"], 2)
        self.assertEqual(decoded["features"], ["core"])

    def test_version_one_peer_is_refused(self):
        code, frames, _ = run_cnode([frame(version=1, min_version=1)])
        self.assertEqual(code, 2)
        self.assertEqual(frames[-1], {"type": "bye", "reason": "version"})

    def test_newer_peer_with_overlapping_range_is_accepted(self):
        code, frames, err = run_cnode([frame(version=5, min_version=2)])
        self.assertEqual(code, 0, err)
        self.assertIn("at version 2", err)

    def test_peer_whose_minimum_is_above_ours_is_refused(self):
        code, frames, _ = run_cnode([frame(version=9, min_version=9)])
        self.assertEqual(code, 2)
        self.assertEqual(frames[-1], {"type": "bye", "reason": "version"})

    def test_missing_min_version_means_version(self):
        f = proto.hello("py-peer", "ssh")
        del f["min_version"]
        code, _, err = run_cnode([json.dumps(f) + "\n"])
        self.assertEqual(code, 0, err)

    def test_own_node_id_is_a_self_link(self):
        code, frames, _ = run_cnode([frame(node="cnode-1")], node="cnode-1")
        self.assertEqual(code, 2)
        self.assertEqual(frames[-1], {"type": "bye", "reason": "self-link"})

    def test_garbage_is_bad_frame(self):
        code, frames, _ = run_cnode(["not json\n"])
        self.assertEqual(code, 2)
        self.assertEqual(frames[-1], {"type": "bye", "reason": "bad-frame"})

    def test_non_hello_first_frame_is_bad_frame(self):
        code, frames, _ = run_cnode(['{"type": "bye", "reason": "x"}\n'])
        self.assertEqual(code, 2)
        self.assertEqual(frames[-1]["reason"], "bad-frame")

    def test_bad_node_name_is_bad_frame(self):
        code, frames, _ = run_cnode([frame(node="bad node!")])
        self.assertEqual(code, 2)
        self.assertEqual(frames[-1]["reason"], "bad-frame")

    def test_features_must_be_strings(self):
        code, frames, _ = run_cnode([frame(features=[1])])
        self.assertEqual(code, 2)
        self.assertEqual(frames[-1]["reason"], "bad-frame")

    def test_python_rejects_the_same_bad_names(self):
        # The C and Python name rules must agree on the same inputs.
        for name in ("bad node!", "x" * 65, ""):
            with self.assertRaises(proto.ProtoError):
                line = json.dumps({"type": "hello", "node": name, "version": 2, "link": "ssh"}) + "\n"
                proto.decode(line.encode("ascii"))


@unittest.skipUnless(_which("make") and (_which("cc") or _which("gcc")),
                     "needs make and a C compiler")
class LeafTest(unittest.TestCase):
    """After the handshake the node stays open: it announces and answers pings."""

    @classmethod
    def setUpClass(cls):
        subprocess.check_call(["make", "-s", "-C", CNODE_DIR, "cnode"])

    def kinds(self, frames):
        return [f.get("kind", f.get("type")) for f in frames]

    def test_announces_itself_on_link_up(self):
        code, frames, err = run_cnode([frame()])
        self.assertEqual(code, 0, err)
        announce = frames[1]
        self.assertEqual(announce["kind"], "announce")
        self.assertEqual(announce["src"], "cnode-1")
        self.assertEqual(announce["dst"], "*")
        self.assertEqual(announce["payload"]["links"], ["py-peer"])
        self.assertEqual(announce["payload"]["features"], ["core"])
        self.assertIsInstance(announce["payload"]["seq"], int)
        self.assertGreater(announce["payload"]["seq"], 0)

    def test_ping_to_self_gets_a_pong_with_the_same_t(self):
        code, frames, err = run_cnode([frame(), packet("cnode-1", payload={"t": 12.5})])
        self.assertEqual(code, 0, err)
        pong = frames[-1]
        self.assertEqual(pong["kind"], "pong")
        self.assertEqual(pong["src"], "cnode-1")
        self.assertEqual(pong["dst"], "py-peer")
        self.assertEqual(pong["payload"], {"t": 12.5})
        # The reply must decode as a valid packet in Python.
        proto.decode((json.dumps(pong) + "\n").encode("ascii"))

    def test_ping_in_a_multicast_list_is_answered(self):
        code, frames, _ = run_cnode([frame(), packet(["other", "cnode-1"])])
        self.assertEqual(self.kinds(frames)[-1], "pong")

    def test_ping_for_another_node_is_not_answered(self):
        code, frames, _ = run_cnode([frame(), packet("other")])
        self.assertEqual(code, 0)
        self.assertEqual(self.kinds(frames), ["hello", "announce"])

    def test_flood_is_not_answered(self):
        code, frames, _ = run_cnode([frame(), packet("*")])
        self.assertEqual(self.kinds(frames), ["hello", "announce"])

    def test_missing_or_bad_t_becomes_null(self):
        code, frames, _ = run_cnode([frame(), packet("cnode-1", payload={})])
        self.assertIsNone(frames[-1]["payload"]["t"])

    def test_announces_repeat_on_the_interval(self):
        # With interval 0 the node announces on every pass of its loop, so the pong
        # comes among repeated announces. The announce order is not fixed.
        code, frames, _ = run_cnode([frame(), packet("cnode-1")], extra=["--announce-interval", "0"])
        kinds = self.kinds(frames)
        self.assertEqual(kinds[:2], ["hello", "announce"])
        self.assertEqual(kinds[-1] in ("pong", "announce"), True)
        self.assertIn("pong", kinds)
        self.assertGreater(kinds.count("announce"), 1)

    def test_bye_from_peer_closes_cleanly(self):
        code, frames, err = run_cnode([frame(), '{"type": "bye", "reason": "x"}\n', packet("cnode-1")])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.kinds(frames), ["hello", "announce"])

    def test_bad_packet_is_refused(self):
        for bad in (packet("cnode-1", ttl=0), packet("cnode-1", ttl=65), packet("bad node!"),
                    packet("cnode-1", src="bad node!"), packet("cnode-1", payload=[1]),
                    packet(["a", "a"]), packet([]), packet("cnode-1", id=5)):
            code, frames, _ = run_cnode([frame(), bad])
            self.assertEqual(code, 2, bad)
            self.assertEqual(frames[-1], {"type": "bye", "reason": "bad-frame"}, bad)

    def test_second_hello_and_unknown_frame_are_refused(self):
        for bad in (frame(), '{"type": "nope"}\n', "not json\n"):
            code, frames, _ = run_cnode([frame(), bad])
            self.assertEqual(code, 2, bad)
            self.assertEqual(frames[-1]["reason"], "bad-frame")

    def test_peer_closing_after_handshake_is_clean(self):
        code, frames, _ = run_cnode([frame()])
        self.assertEqual(code, 0)


@unittest.skipUnless(_which("make") and (_which("cc") or _which("gcc")),
                     "needs make and a C compiler")
class LiveTest(unittest.TestCase):
    """A real Python node links to the C node over an ssh-style link (a fake ssh runs
    the C binary locally). The Python node must see the C node alive."""

    @classmethod
    def setUpClass(cls):
        subprocess.check_call(["make", "-s", "-C", CNODE_DIR, "cnode"])
        cls.tmp = tempfile.mkdtemp()
        cls.home = os.path.join(cls.tmp, "home")
        cls.bin = os.path.join(cls.tmp, "bin")
        os.makedirs(cls.home)
        os.makedirs(cls.bin)
        with open(os.path.join(cls.bin, "ssh"), "w") as fh:
            # Drop ssh options and the target, then run the remote command locally.
            fh.write('#!/bin/sh\nwhile [ "$1" != "" ]; do case "$1" in -o|-p|-i) shift 2 ;; -*) shift ;; *) break ;; esac; done\n'
                     'shift\nexec sh -c "$*"\n')
        os.chmod(os.path.join(cls.bin, "ssh"), 0o755)
        cls.env = dict(os.environ, PATH=cls.bin + os.pathsep + os.environ["PATH"])
        cls.listen = free_port()
        cls.control = free_port()
        config = {"node": "live-py", "authority": "live-py", "listen": cls.listen,
                  "control_port": cls.control, "allow": ["127.0.0.1"],
                  "peers": [{"kind": "ssh", "target": "cnode@localhost",
                             "command": "%s --stdio --node cn1" % BINARY}]}
        with open(os.path.join(cls.home, "config.json"), "w") as fh:
            json.dump(config, fh)
        cls.agentipc = [AGENT_PYTHON, os.path.join(REPO, "bin", "agentipc"), "--home", cls.home]
        cls.log = os.path.join(cls.tmp, "node.log")
        cls.node = subprocess.Popen(cls.agentipc + ["node", "run"], env=cls.env,
                                    stdout=open(cls.log, "w"), stderr=subprocess.STDOUT)

    @classmethod
    def tearDownClass(cls):
        cls.node.terminate()
        cls.node.wait()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def nodes(self):
        proc = subprocess.Popen(self.agentipc + ["bridge", "client"], env=self.env,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out, _ = communicate(proc, 20, b"NODES\n")
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, "bridge client")
        out = out.decode("utf-8")
        return [json.loads(line[5:]) for line in out.splitlines() if line.startswith("NODE ")]

    def test_python_node_sees_the_c_node_alive(self):
        deadline = time.time() + 60
        rows = []
        while time.time() < deadline:
            try:
                rows = self.nodes()
            except (subprocess.CalledProcessError, AssertionError):
                rows = []
            if any(r["node"] == "cn1" and r["alive"] for r in rows):
                break
            time.sleep(1.0)
        with open(self.log) as fh:
            log = fh.read()[-2000:]
        self.assertTrue(any(r["node"] == "cn1" and r["alive"] for r in rows),
                        "cn1 not alive; node rows: %r\nnode log:\n%s" % (rows, log))


@unittest.skipUnless(_which("make") and (_which("cc") or _which("gcc")),
                     "needs make and a C compiler")
class ListenTest(unittest.TestCase):
    """Loopback direct links: the node listens, a Python dialer connects with MODE link."""

    @classmethod
    def setUpClass(cls):
        subprocess.check_call(["make", "-s", "-C", CNODE_DIR, "cnode"])
        cls.port = free_port()
        cls.proc = subprocess.Popen([BINARY, "--listen", str(cls.port), "--node", "cn-l"],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=0.5).close()
                break
            except socket.error:
                time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.proc.kill()
        cls.proc.wait()

    def dial(self, first_line):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        sock.sendall(first_line)
        return sock, sock.makefile("rb")

    def test_direct_link_handshake_announce_and_pong(self):
        for attempt in (1, 2):  # the node serves again after a link ends
            sock, reader = self.dial(b"MODE link\n" +
                                     (json.dumps(proto.hello("py-peer", "direct")) + "\n").encode("ascii") +
                                     packet("cn-l", payload={"t": 3.5}).encode("ascii"))
            lines = [json.loads(reader.readline().decode("ascii")) for _ in range(3)]
            sock.close()
            self.assertEqual(lines[0]["node"], "cn-l", attempt)
            self.assertEqual(lines[1]["kind"], "announce", attempt)
            self.assertEqual(lines[2]["kind"], "pong", attempt)
            self.assertEqual(lines[2]["payload"], {"t": 3.5}, attempt)

    def test_non_link_connection_is_closed(self):
        sock, reader = self.dial(b"MODE client\n")
        sock.settimeout(5)
        self.assertEqual(reader.readline(), b"")
        sock.close()


class FakePeer(object):
    """A Python peer on a direct link to the C node. It sends and reads frames raw."""

    def __init__(self, port, node):
        self.node = node
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.reader = self.sock.makefile("rb")
        self.sock.sendall(b"MODE link\n" + (json.dumps(proto.hello(node, "direct")) + "\n").encode("ascii"))
        self.hello = self.read()

    def send(self, frame):
        self.sock.sendall((json.dumps(frame) + "\n").encode("ascii"))

    def read(self):
        return json.loads(self.reader.readline().decode("ascii"))

    def frames(self, wait=1.0):
        """Every frame that arrives within wait seconds."""
        out = []
        self.sock.settimeout(wait)
        try:
            while True:
                line = self.reader.readline()
                if not line:
                    break
                out.append(json.loads(line.decode("ascii")))
        except (socket.timeout, OSError):
            pass
        self.sock.settimeout(5)
        return out

    def close(self):
        self.reader.close()
        self.sock.close()


def pkt(src, dst, kind, payload=None, pid=None, ttl=16):
    return {"type": "packet", "src": src, "dst": dst, "id": pid or proto.new_id(), "ttl": ttl,
            "kind": kind, "payload": {"t": 1.5} if payload is None else payload}


def announce(src, links, seq, pid=None):
    return pkt(src, "*", "announce", {"seq": seq, "links": links, "features": ["core"]}, pid=pid)


@unittest.skipUnless(_which("make") and (_which("cc") or _which("gcc")),
                     "needs make and a C compiler")
class RouterTest(unittest.TestCase):
    """The C node as a router: several direct links, forwarding, replies and dedupe.
    Peers are fake: P1 and P2 are Python sockets, and far is a node behind P2."""

    @classmethod
    def setUpClass(cls):
        subprocess.check_call(["make", "-s", "-C", CNODE_DIR, "cnode"])

    def start(self):
        self.port = free_port()
        self.proc = subprocess.Popen([BINARY, "--listen", str(self.port), "--node", "rx",
                                      "--announce-interval", "3600"],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.5).close()
                return
            except socket.error:
                time.sleep(0.05)

    def setUp(self):
        self.start()
        self.p1 = FakePeer(self.port, "p1")
        self.p2 = FakePeer(self.port, "p2")
        # p2 announces that it reaches far, and far announces that it reaches p2.
        self.p2.send(announce("p2", ["rx", "far"], 1))
        self.p2.send(announce("far", ["p2"], 1))
        time.sleep(0.5)

    def tearDown(self):
        self.p1.close()
        self.p2.close()
        self.proc.kill()
        self.proc.wait()

    def test_a_packet_is_forwarded_over_two_hops(self):
        self.p1.send(pkt("p1", "far", "ping", pid="route-1"))
        got = [f for f in self.p2.frames() if f.get("id") == "route-1"]
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["dst"], "far")
        self.assertEqual(got[0]["ttl"], 15)

    def test_the_reply_comes_back_the_same_way(self):
        self.p2.send(pkt("far", "p1", "pong", {"t": 2.5}, pid="back-1"))
        got = [f for f in self.p1.frames() if f.get("id") == "back-1"]
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["dst"], "p1")

    def test_a_ping_to_the_node_itself_is_answered_on_its_link(self):
        self.p1.send(pkt("p1", "rx", "ping", {"t": 9.0}, pid="self-1"))
        got = self.p1.frames()
        self.assertEqual([f["kind"] for f in got if f.get("src") == "rx" and f["kind"] == "pong"], ["pong"])

    def test_a_duplicate_is_forwarded_once(self):
        for _ in range(3):
            self.p1.send(pkt("p1", "far", "ping", pid="dup-1"))
        got = [f for f in self.p2.frames() if f.get("id") == "dup-1"]
        self.assertEqual(len(got), 1)

    def test_multicast_goes_on_and_is_answered_locally(self):
        self.p1.send(pkt("p1", ["far", "rx"], "ping", pid="multi-1"))
        forwarded = [f for f in self.p2.frames() if f.get("id") == "multi-1"]
        self.assertEqual(len(forwarded), 1)
        self.assertEqual(forwarded[0]["dst"], ["far"])
        answers = [f for f in self.p1.frames() if f.get("kind") == "pong" and f.get("src") == "rx"]
        self.assertEqual(len(answers), 1)

    def test_a_flood_is_relayed_to_the_other_links(self):
        self.p2.send(announce("far2", ["p2"], 1, pid="flood-1"))
        got = [f for f in self.p1.frames() if f.get("id") == "flood-1"]
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["ttl"], 15)

    def test_no_route_means_no_forwarding(self):
        self.p1.send(pkt("p1", "nowhere", "ping", pid="lost-1"))
        self.assertEqual([f for f in self.p2.frames() + self.p1.frames() if f.get("id") == "lost-1"], [])

    def test_ttl_one_is_not_forwarded(self):
        self.p1.send(pkt("p1", "far", "ping", pid="last-1", ttl=1))
        self.assertEqual([f for f in self.p2.frames() if f.get("id") == "last-1"], [])

    def test_a_reconnected_peer_replaces_the_old_link(self):
        self.p2.close()
        self.p2 = FakePeer(self.port, "p2")
        self.p2.send(announce("p2", ["rx", "far"], 2))
        time.sleep(0.5)
        self.p1.send(pkt("p1", "far", "ping", pid="again-1"))
        got = [f for f in self.p2.frames() if f.get("id") == "again-1"]
        self.assertEqual(len(got), 1)



if __name__ == "__main__":
    unittest.main()
