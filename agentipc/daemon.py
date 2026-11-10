# -*- coding: utf-8 -*-
"""Runs one node: local control socket, direct listener and peer dialers.

Connections arrive in two ways:
- through `agentipc bridge link|client` on this machine, which ssh starts on
  behalf of a remote peer or a client; the bridge pipes stdin and stdout to the
  control socket, which listens on 127.0.0.1 only;
- directly over TCP, for the retro LAN machines, only from addresses in `allow`.

Each connection begins with one line, `MODE link` or `MODE client`.
"""

import os
import socket
import subprocess
import sys
import threading
import time

from agentipc import auth, files, shell, vserver
from agentipc.node import Node

CONTROL_HOST = "127.0.0.1"
DEFAULT_CONTROL_PORT = 7421
DEFAULT_SSH_COMMAND = "agentipc bridge link"
MAX_BACKOFF = 60.0
MODE_LINE_LIMIT = 64


def log(msg):
    sys.stderr.write("agentipc: %s\n" % msg)
    sys.stderr.flush()


def _shutdown(sock):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except socket.error:
        pass
    sock.close()


class Daemon(object):
    def __init__(self, node_id, cfg, home):
        self.cfg = cfg
        self.home = home
        self.node = Node(node_id, authority=cfg.get("authority"))
        self.control_port = cfg.get("control_port", DEFAULT_CONTROL_PORT)
        self.listen_port = None
        self.authority = auth.install(
            self.node, auth.Accounts(os.path.join(home, "accounts.json")) if self.node.is_authority() else None)
        self.vs = vserver.VirtualServer(self.node, os.path.join(home, "mesh", "records.jsonl"))
        self.files = files.FileService(self.node, os.path.join(home, "inbox"))
        self.shell = shell.ShellService(self.node, cfg.get("shell"))
        self._stopping = threading.Event()
        self._sockets = []

    # ----- lifecycle ---------------------------------------------------

    def start(self):
        self.node.start()
        self._listen_control()
        if self.cfg.get("allow") and self.cfg.get("listen"):
            self._listen_direct()
        for peer in self.cfg.get("peers", []):
            if peer.get("kind") == "ssh":
                _thread(self._ssh_dialer, peer)
            elif peer.get("kind") == "direct":
                _thread(self._direct_dialer, peer)
            else:
                log("ignoring peer with unknown kind %r" % peer.get("kind"))

    def stop(self):
        self._stopping.set()
        self.node.stop()
        for sock in self._sockets:
            _shutdown(sock)
        with self.node.lock:
            links = list(self.node.attached)
        for link in links:
            link.close()

    # ----- listeners ---------------------------------------------------

    def _listen_control(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((CONTROL_HOST, self.control_port))
        sock.listen(16)
        self.control_port = sock.getsockname()[1]
        self._sockets.append(sock)
        _thread(self._accept_loop, sock, None)

    def _listen_direct(self):
        allow = set(self.cfg.get("allow", []))
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", self.cfg["listen"]))
        sock.listen(16)
        self.listen_port = sock.getsockname()[1]
        self._sockets.append(sock)
        _thread(self._accept_loop, sock, allow)

    def _accept_loop(self, sock, allow):
        while not self._stopping.is_set():
            try:
                conn, addr = sock.accept()
            except (socket.error, OSError):
                break
            if allow is not None and addr[0] not in allow:
                log("refused direct connection from %s" % addr[0])
                _shutdown(conn)
                continue
            _thread(self._serve, conn, "direct" if allow is not None else "ssh")

    def _serve(self, conn, kind):
        """Read the MODE line, then hand the connection to a link or a client session."""
        rfile = conn.makefile("rb")
        wfile = conn.makefile("wb")
        try:
            conn.settimeout(10)
            mode = rfile.readline(MODE_LINE_LIMIT).decode("ascii", "replace").split()
            conn.settimeout(None)
        except (socket.error, OSError, ValueError):
            _shutdown(conn)
            return
        if mode == ["MODE", "link"]:
            self.node.make_link(rfile, wfile, kind, closer=lambda: _shutdown(conn))
        elif mode == ["MODE", "client"]:
            try:
                vserver.ClientSession(self.vs, rfile, wfile).run()
            finally:
                _shutdown(conn)
        else:
            _shutdown(conn)

    # ----- dialers -----------------------------------------------------

    def _ssh_dialer(self, peer):
        target = peer["target"]
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=30",
                "-p", str(peer.get("port", 22))]
        if peer.get("key"):
            argv += ["-i", peer["key"]]
        argv += [target, peer.get("command", DEFAULT_SSH_COMMAND)]
        self._redial(lambda: self._open_ssh(argv, target))

    def _open_ssh(self, argv, target):
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE)

        def close_proc():
            if proc.poll() is None:
                proc.kill()
            proc.wait()

        link = self.node.make_link(proc.stdout, proc.stdin, "ssh", closer=close_proc)
        log("ssh link to %s started" % target)
        return link

    def _direct_dialer(self, peer):
        self._redial(lambda: self._open_direct(peer["host"], peer["port"]))

    def _open_direct(self, host, port):
        sock = socket.create_connection((host, port), timeout=10)
        sock.settimeout(None)
        rfile = sock.makefile("rb")
        wfile = sock.makefile("wb")
        wfile.write(b"MODE link\n")
        wfile.flush()
        return self.node.make_link(rfile, wfile, "direct", closer=lambda: _shutdown(sock))

    def _redial(self, open_link):
        """Keep one link open to a peer, reconnecting with backoff."""
        delay = 1.0
        while not self._stopping.is_set():
            started = time.time()
            try:
                link = open_link()
            except (OSError, socket.error, ValueError) as exc:
                log("link attempt failed: %s" % exc)
                link = None
            if link is not None:
                link.closed_event.wait()
                if time.time() - started > MAX_BACKOFF:
                    delay = 1.0
            if self._stopping.wait(delay):
                break
            delay = min(delay * 2, MAX_BACKOFF)


def _thread(target, *args):
    thread = threading.Thread(target=target, args=args)
    thread.daemon = True
    thread.start()
    return thread
