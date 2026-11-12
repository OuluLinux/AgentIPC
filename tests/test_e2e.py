# -*- coding: utf-8 -*-
"""End to end: node processes, the agentipc CLI, ssh links and direct links.

ssh is replaced by a fake script on PATH. It runs the remote command locally,
with AGENTIPC_HOME set to the target's home, which is what sshd would do.
"""

import atexit
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
# Interpreter for the nodes and the CLI under test. AGENTIPC_PYTHON=python2.6 runs them on 2.6.
AGENT_PYTHON = os.environ.get("AGENTIPC_PYTHON", sys.executable)
sys.path.insert(0, REPO)

from agentipc import auth  # noqa: E402
from mesh_util import communicate  # noqa: E402

# Node processes started by these tests. If a run is interrupted, the hook below
# still kills whatever is left at exit, so no node outlives the test run.
_PROCS = []


def spawn(argv, **kwargs):
    proc = subprocess.Popen(argv, **kwargs)
    _PROCS.append(proc)
    return proc


def _kill_leftovers():
    for proc in _PROCS:
        if proc.poll() is None:
            proc.kill()


atexit.register(_kill_leftovers)

FAKE_SSH = """#!/bin/sh
# Test double for ssh: run the remote command locally, in the target's home.
while [ "$1" != "" ]; do
  case "$1" in
    -o|-p|-i) shift 2 ;;
    -*) shift ;;
    *) break ;;
  esac
done
target="$1"; shift
name="${target%%@*}"
export AGENTIPC_HOME="$FAKE_SSH_HOMES/$name"
exec sh -c "$*"
"""


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def wait_port(port, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return True
        except socket.error:
            time.sleep(0.05)
    return False


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.homes = os.path.join(cls.tmp, "homes")
        cls.bin = os.path.join(cls.tmp, "bin")
        os.makedirs(cls.homes)
        os.makedirs(cls.bin)
        with open(os.path.join(cls.bin, "ssh"), "w") as fh:
            fh.write(FAKE_SSH)
        os.chmod(os.path.join(cls.bin, "ssh"), 0o755)
        with open(os.path.join(cls.bin, "agentipc"), "w") as fh:
            fh.write('#!/bin/sh\nexec "%s" "%s/bin/agentipc" "$@"\n' % (AGENT_PYTHON, REPO))
        os.chmod(os.path.join(cls.bin, "agentipc"), 0o755)
        cls.env = dict(os.environ)
        cls.env["PATH"] = cls.bin + os.pathsep + os.environ["PATH"]
        cls.env["FAKE_SSH_HOMES"] = cls.homes
        cls.procs = []
        cls.ports = {}
        for name in ("AUTH", "A", "C"):
            cls.ports[name] = free_port()
        cls.write_config("AUTH", {"authority": "AUTH"})
        cls.write_config("A", {"authority": "AUTH", "peers": [{"kind": "ssh", "target": "AUTH@localhost"}]})
        cls.write_config("C", {"authority": "AUTH", "peers": [{"kind": "ssh", "target": "A@localhost"}],
                               "shell": {"enabled": True, "users": ["alice"]}})
        accounts = auth.Accounts(os.path.join(cls.homes, "AUTH", "accounts.json"))
        accounts.add_user("alice", "alice-pw")
        accounts.add_user("bob", "bob-pw")
        accounts.add_server("plain", encrypted=False)
        for name in ("AUTH", "A", "C"):
            cls.procs.append(cls.start_node(name))
        for name in ("AUTH", "A", "C"):
            assert wait_port(cls.ports[name]), name
        time.sleep(1.5)  # let the ssh links and announcements settle

    @classmethod
    def write_config(cls, name, extra):
        import json
        home = os.path.join(cls.homes, name)
        os.makedirs(home)
        cfg = {"node": name, "control_port": cls.ports[name]}
        cfg.update(extra)
        with open(os.path.join(home, "config.json"), "w") as fh:
            json.dump(cfg, fh)

    @classmethod
    def start_node(cls, name):
        env = dict(cls.env, AGENTIPC_HOME=os.path.join(cls.homes, name))
        log = open(os.path.join(cls.tmp, name + ".log"), "ab")
        return spawn([AGENT_PYTHON, os.path.join(REPO, "bin", "agentipc"), "node", "run"],
                                env=env, stdout=log, stderr=log)

    @classmethod
    def tearDownClass(cls):
        for proc in cls.procs:
            proc.terminate()
            proc.wait()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def vs(self, *args, **kwargs):
        env = dict(self.env, PW="alice-pw")
        cmd = [AGENT_PYTHON, os.path.join(REPO, "bin", "agentipc"), "vs"] + list(args)
        return subprocess.check_output(cmd, env=env, stderr=subprocess.STDOUT).decode("utf-8")

    def test_message_crosses_two_ssh_links(self):
        self.vs("--ssh", "AUTH@localhost", "--user", "alice", "--server", "plain",
                "--password-env", "PW", "say", "general", "hello from AUTH")
        out = self.vs("--ssh", "C@localhost", "--user", "alice", "--server", "plain",
                      "--password-env", "PW", "read", "general")
        self.assertIn("hello from AUTH", out)

    def test_node_list_shows_the_mesh(self):
        out = self.vs("--ssh", "C@localhost", "--user", "alice", "--server", "plain",
                      "--password-env", "PW", "nodes")
        self.assertIn("AUTH", out)
        self.assertIn("A", out)

    def test_wrong_password_exits_with_error(self):
        env = dict(self.env, PW="nope")
        proc = subprocess.Popen([AGENT_PYTHON, os.path.join(REPO, "bin", "agentipc"), "vs",
                                 "--ssh", "A@localhost", "--user", "alice", "--server", "plain",
                                 "--password-env", "PW", "read", "general"],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _, err = communicate(proc, 20)
        self.assertEqual(proc.returncode, 1)
        self.assertIn(b"bad-login", err)

    def exec_as(self, user, password, via, node, command):
        env = dict(self.env, PW=password)
        cmd = [AGENT_PYTHON, os.path.join(REPO, "bin", "agentipc"), "vs", "--ssh", via + "@localhost",
               "--user", user, "--server", "plain", "--password-env", "PW",
               "exec", node, "--timeout", "10", "--"] + command
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = communicate(proc, 30)
        return proc.returncode, out.decode("utf-8"), err.decode("utf-8")

    def test_exec_runs_where_shell_is_enabled(self):
        code, out, err = self.exec_as("alice", "alice-pw", "A", "C", ["echo", "hi-from-C;", "exit", "3"])
        self.assertEqual(out, "hi-from-C\n")
        self.assertEqual(code, 3)

    def test_exec_refused_where_shell_is_disabled(self):
        code, out, err = self.exec_as("alice", "alice-pw", "A", "A", ["echo", "nope"])
        self.assertEqual(out, "")
        self.assertIn("shell-disabled", err)
        self.assertEqual(code, 1)

    def test_exec_refused_for_a_user_not_on_the_list(self):
        code, out, err = self.exec_as("bob", "bob-pw", "A", "C", ["echo", "nope"])
        self.assertEqual(out, "")
        self.assertIn("not-allowed", err)

    def test_file_crosses_the_mesh(self):
        src = os.path.join(self.tmp, "payload.bin")
        data = os.urandom(100 * 1024)
        with open(src, "wb") as fh:
            fh.write(data)
        self.vs("--ssh", "A@localhost", "--user", "alice", "--server", "plain",
                "--password-env", "PW", "sendfile", "AUTH", src)
        inbox = os.path.join(self.homes, "AUTH", "inbox")
        received = None
        deadline = time.time() + 10
        while time.time() < deadline and received is None:
            for root, _, files in os.walk(inbox) if os.path.isdir(inbox) else []:
                for name in files:
                    if name == "payload.bin":
                        received = os.path.join(root, name)
            time.sleep(0.1)
        self.assertIsNotNone(received)
        self.assertIn(os.sep + "alice@", received)
        with open(received, "rb") as fh:
            self.assertEqual(fh.read(), data)


class DirectLinks(unittest.TestCase):
    """Plain TCP links: allowed from listed addresses only, and they reconnect."""

    @classmethod
    def setUpClass(cls):
        import json
        cls.tmp = tempfile.mkdtemp()
        cls.homes = os.path.join(cls.tmp, "homes")
        os.makedirs(cls.homes)
        cls.env = dict(os.environ, FAKE_SSH_HOMES=cls.homes)
        cls.ports = {n: free_port() for n in ("D", "G")}
        configs = {
            "D": {"authority": "D", "listen": cls.ports["D"], "allow": ["127.0.0.1"]},
            "E": {"authority": "D", "peers": [{"kind": "direct", "host": "127.0.0.1", "port": cls.ports["D"]}]},
            "G": {"authority": "G", "listen": cls.ports["G"], "allow": ["10.9.9.9"]},
        }
        for name, extra in configs.items():
            home = os.path.join(cls.homes, name)
            os.makedirs(home)
            cfg = {"node": name, "control_port": free_port()}
            cfg.update(extra)
            with open(os.path.join(home, "config.json"), "w") as fh:
                json.dump(cfg, fh)
        accounts = auth.Accounts(os.path.join(cls.homes, "D", "accounts.json"))
        accounts.add_user("alice", "alice-pw")
        accounts.add_server("plain", encrypted=False)
        accounts_g = auth.Accounts(os.path.join(cls.homes, "G", "accounts.json"))
        accounts_g.add_user("alice", "alice-pw")
        accounts_g.add_server("plain", encrypted=False)
        cls.procs = {name: cls.start(name) for name in ("D", "E", "G")}
        for name in ("D", "G"):
            assert wait_port(cls.ports[name]), name
        time.sleep(1.5)

    @classmethod
    def start(cls, name):
        env = dict(cls.env, AGENTIPC_HOME=os.path.join(cls.homes, name))
        log = open(os.path.join(cls.tmp, name + ".log"), "ab")
        return spawn([AGENT_PYTHON, os.path.join(REPO, "bin", "agentipc"), "node", "run"],
                                env=env, stdout=log, stderr=log)

    @classmethod
    def tearDownClass(cls):
        for proc in cls.procs.values():
            if proc.poll() is None:
                proc.terminate()
                proc.wait()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def direct_nodes(self, port):
        env = dict(self.env, PW="alice-pw")
        cmd = [AGENT_PYTHON, os.path.join(REPO, "bin", "agentipc"), "vs", "--direct",
               "127.0.0.1:%d" % port, "--user", "alice", "--server", "plain",
               "--password-env", "PW", "nodes"]
        return subprocess.check_output(cmd, env=env, stderr=subprocess.STDOUT).decode("utf-8")

    def test_listed_address_links_directly(self):
        self.assertTrue(wait_for_text(lambda: self.direct_nodes(self.ports["D"]), "E\tup"))

    def test_unlisted_address_is_refused(self):
        env = dict(self.env, PW="alice-pw")
        proc = subprocess.Popen([AGENT_PYTHON, os.path.join(REPO, "bin", "agentipc"), "vs", "--direct",
                                 "127.0.0.1:%d" % self.ports["G"], "--user", "alice", "--server", "plain",
                                 "--password-env", "PW", "nodes"],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _, err = communicate(proc, 20)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(b"not-allowed", err)

    def test_direct_link_reconnects_after_the_node_restarts(self):
        self.procs["D"].terminate()
        self.procs["D"].wait()
        self.procs["D"] = self.start("D")
        self.assertTrue(wait_port(self.ports["D"]))
        self.assertTrue(wait_for_text(lambda: self.direct_nodes(self.ports["D"]), "E\tup", timeout=30))


def wait_for_text(fn, needle, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if needle in fn():
                return True
        except subprocess.CalledProcessError:
            pass
        time.sleep(0.3)
    return False


class WatchAndRemote(unittest.TestCase):
    """`vs watch` shows live messages; `remote start` and `send` build the right commands."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.home = os.path.join(cls.tmp, "home")
        cls.bin = os.path.join(cls.tmp, "bin")
        os.makedirs(cls.home)
        os.makedirs(cls.bin)
        cls.env = dict(os.environ, PW="123", AGENTIPC_HOME=cls.home)
        cls.port = free_port()
        with open(os.path.join(cls.home, "config.json"), "w") as fh:
            json.dump({"node": "solo", "authority": "solo", "allow": ["127.0.0.1"], "listen": cls.port}, fh)
        cls.agentipc = [AGENT_PYTHON, os.path.join(REPO, "bin", "agentipc")]
        subprocess.check_call(cls.agentipc + ["--home", cls.home, "account", "add", "user", "--password-env", "PW"],
                              env=cls.env)
        subprocess.check_call(cls.agentipc + ["--home", cls.home, "server", "create", "plain"], env=cls.env)
        cls.control = free_port()
        cls.node = spawn(cls.agentipc + ["--home", cls.home, "node", "run", "--node", "solo",
                                         "--control-port", str(cls.control)],
                         env=cls.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert wait_port(cls.port)
        time.sleep(1.0)

    @classmethod
    def tearDownClass(cls):
        cls.node.terminate()
        cls.node.wait()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def vs(self, *args):
        return subprocess.check_output(self.agentipc + ["vs", "--direct", "127.0.0.1:%d" % self.port,
                                                        "--user", "user", "--server", "plain",
                                                        "--password-env", "PW"] + list(args),
                                       env=self.env, stderr=subprocess.STDOUT).decode("utf-8")

    def test_watch_shows_a_live_message(self):
        watcher = spawn(self.agentipc + ["vs", "--direct", "127.0.0.1:%d" % self.port, "--user", "user",
                                         "--server", "plain", "--password-env", "PW", "watch", "general"],
                        env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(2.0)
        self.vs("say", "general", "watch-test-message")
        time.sleep(2.0)
        watcher.kill()
        out, err = watcher.communicate()
        self.assertIn(b"watch-test-message", out, err)

    def test_wait_returns_the_next_live_message(self):
        waiter = spawn(self.agentipc + ["vs", "--direct", "127.0.0.1:%d" % self.port, "--user", "user",
                                        "--server", "plain", "--password-env", "PW", "wait", "general"],
                       env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(2.0)
        self.vs("say", "general", "wait-test-message")
        out, err = communicate(waiter, 20)
        self.assertEqual(waiter.returncode, 0, err)
        self.assertIn(b"wait-test-message", out)
        self.assertNotIn(b"watch-test-message", out)

    def test_wait_from_someone_else_keeps_waiting(self):
        waiter = spawn(self.agentipc + ["vs", "--direct", "127.0.0.1:%d" % self.port, "--user", "user",
                                        "--server", "plain", "--password-env", "PW", "wait", "general",
                                        "--from", "nobody-here"],
                       env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(2.0)
        self.vs("say", "general", "ignored-by-filter")
        time.sleep(2.0)
        self.assertIsNone(waiter.poll(), "wait returned for a message from someone else")
        waiter.kill()
        waiter.communicate()

    def test_control_port_refuses_a_connection_without_the_secret(self):
        import socket
        sock = socket.create_connection(("127.0.0.1", self.control), timeout=10)
        sock.sendall(b"MODE client\nVERSION 1\n")
        self.assertEqual(sock.makefile("rb").readline(), b"")
        sock.close()

    def test_control_port_accepts_the_secret(self):
        import socket
        with open(os.path.join(self.home, "control.token")) as fh:
            secret = fh.read().strip()
        sock = socket.create_connection(("127.0.0.1", self.control), timeout=10)
        sock.sendall(("MODE client %s\nVERSION 1\n" % secret).encode("ascii"))
        self.assertEqual(sock.makefile("rb").readline(), b"VERSION 1\n")
        sock.close()

    def test_reply_arrives_after_the_client_closes_its_side(self):
        import socket
        with open(os.path.join(self.home, "control.token")) as fh:
            secret = fh.read().strip()
        sock = socket.create_connection(("127.0.0.1", self.control), timeout=10)
        sock.sendall(("MODE client %s\nVERSION 1\n" % secret).encode("ascii"))
        sock.shutdown(socket.SHUT_WR)
        self.assertEqual(sock.makefile("rb").readline(), b"VERSION 1\n")
        sock.close()

    def test_client_version_negotiation(self):
        import socket
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        sock.sendall(b"MODE client\nVERSION 1\nVERSION 9\nVERSION 0\nVERSION x\n")
        reader = sock.makefile("rb")
        lines = [reader.readline() for _ in range(4)]
        sock.close()
        self.assertEqual([l.decode().strip() for l in lines],
                         ["VERSION 1", "VERSION 1", "ERR version", "ERR version"])

    def recorded(self, name, args):
        """Run `remote`/`send` with a fake ssh or rsync that only writes its argv to a file."""
        log = os.path.join(self.tmp, name + ".argv")
        fake = os.path.join(self.bin, name)
        with open(fake, "w") as fh:
            fh.write('#!/bin/sh\nprintf "%%s\\n" "$@" > "%s"\n' % log)
        os.chmod(fake, 0o755)
        env = dict(self.env, PATH=self.bin + os.pathsep + os.environ["PATH"])
        subprocess.check_call(self.agentipc + args, env=env)
        with open(log) as fh:
            return fh.read().split("\n")[:-1]

    def test_remote_start_builds_an_ssh_command(self):
        argv = self.recorded("ssh", ["remote", "start", "sblo@example.invalid:2222", "--key", "/keys/id"])
        self.assertEqual(argv, ["-o", "BatchMode=yes", "-p", "2222", "-i", "/keys/id", "sblo@example.invalid",
                                "nohup agentipc node run >/dev/null 2>&1 </dev/null &"])

    def test_send_builds_an_rsync_command_over_ssh(self):
        argv = self.recorded("rsync", ["send", "sblo@example.invalid", "report.txt", "/tmp/in/"])
        self.assertEqual(argv[:3], ["-a", "-e", "ssh -o BatchMode=yes -p 22"])
        self.assertEqual(argv[3:], ["report.txt", "sblo@example.invalid:/tmp/in/"])


if __name__ == "__main__":
    unittest.main()


class Instances(unittest.TestCase):
    """Two instances on one machine: separate homes and ports, linked over direct TCP."""

    @classmethod
    def setUpClass(cls):
        import json
        cls.tmp = tempfile.mkdtemp()
        cls.env = dict(os.environ, PW="123")
        cls.homes = {n: os.path.join(cls.tmp, n) for n in ("one", "two")}
        cls.ports = {n: free_port() for n in ("one-control", "one-listen", "two-control", "two-listen")}
        for name in cls.homes:
            os.makedirs(cls.homes[name])
        with open(os.path.join(cls.homes["one"], "config.json"), "w") as fh:
            json.dump({"node": "one", "authority": "one", "allow": ["127.0.0.1"]}, fh)
        with open(os.path.join(cls.homes["two"], "config.json"), "w") as fh:
            json.dump({"node": "two", "authority": "one", "allow": ["127.0.0.1"],
                       "peers": [{"kind": "direct", "host": "127.0.0.1", "port": cls.ports["one-listen"]}]}, fh)
        cls.cli("--home", cls.homes["one"], "account", "add", "user", "--password-env", "PW")
        cls.cli("--home", cls.homes["one"], "server", "create", "plain")
        cls.procs = [
            cls.start("one", "one", cls.ports["one-control"], cls.ports["one-listen"]),
            cls.start("two", "two", cls.ports["two-control"], cls.ports["two-listen"]),
        ]
        assert wait_port(cls.ports["one-listen"]) and wait_port(cls.ports["two-listen"])
        time.sleep(1.5)

    @classmethod
    def cli(cls, *args):
        return subprocess.check_output([AGENT_PYTHON, os.path.join(REPO, "bin", "agentipc")] + list(args),
                                       env=cls.env, stderr=subprocess.STDOUT).decode("utf-8")

    @classmethod
    def start(cls, home_name, node, control, listen):
        log = open(os.path.join(cls.tmp, home_name + ".log"), "ab")
        return spawn([AGENT_PYTHON, os.path.join(REPO, "bin", "agentipc"),
                                 "--home", cls.homes[home_name], "node", "run", "--node", node,
                                 "--control-port", str(control), "--listen", str(listen)],
                                env=cls.env, stdout=log, stderr=log)

    @classmethod
    def tearDownClass(cls):
        for proc in cls.procs:
            proc.terminate()
            proc.wait()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def vs_direct(self, port, *args):
        return subprocess.check_output(
            [AGENT_PYTHON, os.path.join(REPO, "bin", "agentipc"), "vs", "--direct", "127.0.0.1:%d" % port,
             "--user", "user", "--server", "plain", "--password-env", "PW"] + list(args),
            env=self.env, stderr=subprocess.STDOUT).decode("utf-8")

    def test_homes_are_separate(self):
        self.assertTrue(os.path.exists(os.path.join(self.homes["one"], "accounts.json")))
        self.assertFalse(os.path.exists(os.path.join(self.homes["two"], "accounts.json")))

    def test_message_crosses_between_instances(self):
        self.assertIn("posted", self.vs_direct(self.ports["one-listen"], "say", "general", "hello two"))
        self.assertTrue(wait_for_text(lambda: self.vs_direct(self.ports["two-listen"], "read", "general"),
                                      "hello two", timeout=20))


class Failover(unittest.TestCase):
    """Authority failover over ssh links: the accounts reach the standby, the standby is
    promoted by hand, logins go through it, and the old authority steps down when it returns."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.homes = os.path.join(self.tmp, "homes")
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.homes)
        os.makedirs(self.bin)
        with open(os.path.join(self.bin, "ssh"), "w") as fh:
            fh.write(FAKE_SSH)
        os.chmod(os.path.join(self.bin, "ssh"), 0o755)
        # The remote command runs "agentipc"; point it at this checkout, not an installed copy.
        with open(os.path.join(self.bin, "agentipc"), "w") as fh:
            fh.write('#!/bin/sh\nexec "%s" "%s/bin/agentipc" "$@"\n' % (AGENT_PYTHON, REPO))
        os.chmod(os.path.join(self.bin, "agentipc"), 0o755)
        self.env = dict(os.environ)
        self.env["PATH"] = self.bin + os.pathsep + os.environ["PATH"]
        self.env["FAKE_SSH_HOMES"] = self.homes
        self.env["PW"] = "alice-pw"
        self.ports = {"AUTH": free_port(), "SB": free_port()}
        self.write_config("AUTH", {"authority": "AUTH", "standby": "SB",
                                   "peers": [{"kind": "ssh", "target": "SB@localhost"}]})
        self.write_config("SB", {"authority": "AUTH", "standby": "AUTH",
                                 "peers": [{"kind": "ssh", "target": "AUTH@localhost"}]})
        accounts = auth.Accounts(os.path.join(self.homes, "AUTH", "accounts.json"))
        accounts.add_user("alice", "alice-pw")
        accounts.add_server("plain", encrypted=False)
        self.procs = {}
        for name in ("AUTH", "SB"):
            self.start(name)
        self.settle()

    def tearDown(self):
        for proc in self.procs.values():
            if proc.poll() is None:
                proc.terminate()
                proc.wait()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_config(self, name, extra):
        home = os.path.join(self.homes, name)
        os.makedirs(home)
        cfg = {"node": name, "control_port": self.ports[name]}
        cfg.update(extra)
        with open(os.path.join(home, "config.json"), "w") as fh:
            json.dump(cfg, fh)

    def start(self, name):
        env = dict(self.env, AGENTIPC_HOME=os.path.join(self.homes, name))
        log = open(os.path.join(self.tmp, name + ".log"), "ab")
        self.procs[name] = spawn([AGENT_PYTHON, os.path.join(REPO, "bin", "agentipc"), "node", "run"],
                                 env=env, stdout=log, stderr=log)
        self.assertTrue(wait_port(self.ports[name]), name)

    def settle(self):
        time.sleep(1.5)  # ssh links and the first announces

    def stop(self, name):
        self.procs[name].terminate()
        self.procs[name].wait()

    def cli(self, home, *args, **kwargs):
        env = dict(self.env, AGENTIPC_HOME=os.path.join(self.homes, home))
        cmd = [AGENT_PYTHON, os.path.join(REPO, "bin", "agentipc")] + list(args)
        return subprocess.check_output(cmd, env=env, stderr=subprocess.STDOUT, **kwargs).decode("utf-8")

    def vs(self, target, *args):
        cmd = [AGENT_PYTHON, os.path.join(REPO, "bin", "agentipc"), "vs", "--ssh", target + "@localhost",
               "--user", "alice", "--server", "plain", "--password-env", "PW"] + list(args)
        return subprocess.check_output(cmd, env=self.env, stderr=subprocess.STDOUT).decode("utf-8")

    def wait_for(self, check, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if check():
                return True
            time.sleep(0.5)
        return False

    def test_the_standby_takes_over_and_the_old_authority_steps_down(self):
        replica = os.path.join(self.homes, "SB", auth.REPLICA_FILE)
        self.assertTrue(self.wait_for(lambda: os.path.exists(replica)), "replica never arrived")
        self.vs("AUTH", "say", "general", "before failover")

        self.stop("AUTH")
        self.cli("SB", "account", "promote")
        self.stop("SB")
        self.start("SB")
        self.settle()

        self.vs("SB", "say", "general", "after failover")
        self.assertIn("after failover", self.vs("SB", "read", "general"))
        promoted = auth.Accounts(os.path.join(self.homes, "SB", "accounts.json")).data
        self.assertEqual((promoted["authority"], promoted["epoch"]), ("SB", 1))

        self.start("AUTH")
        self.settle()
        self.assertTrue(self.wait_for(lambda: auth.Accounts(
            os.path.join(self.homes, "AUTH", "accounts.json")).data["authority"] == "SB"),
            "old authority never stepped down")
        with self.assertRaises(subprocess.CalledProcessError) as cm:
            self.cli("AUTH", "account", "add", "bob", "--password-env", "PW")
        self.assertIn("not the authority", cm.exception.output.decode("utf-8"))

