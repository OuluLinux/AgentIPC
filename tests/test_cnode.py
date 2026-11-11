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

CNODE_DIR = os.path.join(REPO, "cnode")
BINARY = os.path.join(CNODE_DIR, "cnode")


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
    out, err = p.communicate(data, timeout=10)
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


@unittest.skipUnless(shutil.which("make") and (shutil.which("cc") or shutil.which("gcc")),
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


@unittest.skipUnless(shutil.which("make") and (shutil.which("cc") or shutil.which("gcc")),
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

    def test_announce_interval_zero_announces_on_every_ping(self):
        code, frames, _ = run_cnode([frame(), packet("cnode-1")], extra=["--announce-interval", "0"])
        self.assertEqual(self.kinds(frames), ["hello", "announce", "announce", "pong"])

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


@unittest.skipUnless(shutil.which("make") and (shutil.which("cc") or shutil.which("gcc")),
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
        cls.agentipc = [sys.executable, os.path.join(REPO, "bin", "agentipc"), "--home", cls.home]
        cls.log = os.path.join(cls.tmp, "node.log")
        cls.node = subprocess.Popen(cls.agentipc + ["node", "run"], env=cls.env,
                                    stdout=open(cls.log, "w"), stderr=subprocess.STDOUT)

    @classmethod
    def tearDownClass(cls):
        cls.node.terminate()
        cls.node.wait()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def nodes(self):
        out = subprocess.check_output(self.agentipc + ["bridge", "client"], env=self.env,
                                      input=b"NODES\n", timeout=20).decode("utf-8")
        return [json.loads(line[5:]) for line in out.splitlines() if line.startswith("NODE ")]

    def test_python_node_sees_the_c_node_alive(self):
        deadline = time.time() + 60
        rows = []
        while time.time() < deadline:
            try:
                rows = self.nodes()
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                rows = []
            if any(r["node"] == "cn1" and r["alive"] for r in rows):
                break
            time.sleep(1.0)
        with open(self.log) as fh:
            log = fh.read()[-2000:]
        self.assertTrue(any(r["node"] == "cn1" and r["alive"] for r in rows),
                        "cn1 not alive; node rows: %r\nnode log:\n%s" % (rows, log))


@unittest.skipUnless(shutil.which("make") and (shutil.which("cc") or shutil.which("gcc")),
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


if __name__ == "__main__":
    unittest.main()
