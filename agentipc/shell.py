# -*- coding: utf-8 -*-
"""Remote shell commands on a node, only where the node enables them.

A logged-in client asks the virtual server to run a command on a node. The
request carries the client's token. The target node checks the token with the
authority, then checks the user against its own `shell.users` list. Nothing
runs unless `shell.enabled` is true there and the user is listed.

Commands run in a worker thread. Packet handlers run under the node lock, so a
slow command must not run inside one.
"""

import subprocess
import threading

from agentipc import auth

MAX_OUTPUT = 64 * 1024
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 600
MAX_COMMAND = 4096


class ShellService(object):
    def __init__(self, node, policy):
        self.node = node
        self.policy = policy or {}
        node.register("exec", self._h_exec)
        node.register("exec-result", lambda pkt: node.resolve(pkt["payload"]))
        node.add_service(self)

    def on_link_up(self, link):
        pass

    def on_node_gone(self, node_id):
        pass

    def _h_exec(self, pkt):
        p = pkt["payload"]
        src, req = pkt["src"], p.get("req")
        command, token, timeout = p.get("command"), p.get("token"), p.get("timeout", DEFAULT_TIMEOUT)
        if not self.policy.get("enabled"):
            self._reply(src, req, {"error": "shell-disabled"})
            return
        if not (isinstance(command, str_types) and 0 < len(command) <= MAX_COMMAND and
                isinstance(token, str_types) and isinstance(timeout, int) and 0 < timeout <= MAX_TIMEOUT):
            self._reply(src, req, {"error": "bad-arguments"})
            return
        auth.verify_remote(self.node, token, lambda info: self._checked(src, req, info, command, timeout))

    def _checked(self, src, req, info, command, timeout):
        if info is None:
            self._reply(src, req, {"error": "invalid-token"})
            return
        if info["user"] not in self.policy.get("users", []):
            self._reply(src, req, {"error": "not-allowed"})
            return
        worker = threading.Thread(target=self._run, args=(src, req, command, timeout))
        worker.daemon = True
        worker.start()

    def _run(self, src, req, command, timeout):
        result = run_command(command, timeout)
        self._reply(src, req, result)

    def _reply(self, dst, req, body):
        self.node.send(dst, "exec-result", dict(body, req=req))


def run_command(command, timeout):
    """Run a command through the local shell. Returns a dict; output is cut to MAX_OUTPUT."""
    proc = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    done = {}

    def collect():
        done["out"], done["err"] = proc.communicate()

    reader = threading.Thread(target=collect)
    reader.daemon = True
    reader.start()
    reader.join(timeout)
    timed_out = reader.is_alive()
    if timed_out:
        proc.kill()
        reader.join(5)
    out = done.get("out", b"")
    err = done.get("err", b"")
    return {
        "exit": None if timed_out else proc.returncode,
        "stdout": _text(out[:MAX_OUTPUT]),
        "stderr": _text(err[:MAX_OUTPUT]),
        "truncated": len(out) > MAX_OUTPUT or len(err) > MAX_OUTPUT,
        "timed_out": timed_out,
    }


def _text(data):
    return data.decode("utf-8", "replace")


try:
    str_types = (str, unicode)  # noqa: F821  (Python 2)
except NameError:
    str_types = (str,)
