"""The C node's handshake, checked against frames from the Python protocol module."""
import json
import os
import shutil
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO))

from agentipc import proto  # noqa: E402

CNODE_DIR = os.path.join(REPO, "cnode")
BINARY = os.path.join(CNODE_DIR, "cnode")


def run_cnode(lines, node="cnode-1"):
    """Feeds frame lines to the C node on stdin. Returns (exit code, frames out, stderr)."""
    data = "".join(lines).encode("ascii")
    p = subprocess.Popen([BINARY, "--stdio", "--node", node], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = p.communicate(data, timeout=10)
    frames = [json.loads(line) for line in out.decode("ascii").splitlines() if line]
    return p.returncode, frames, err.decode("ascii")


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


if __name__ == "__main__":
    unittest.main()
