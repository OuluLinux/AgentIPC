# -*- coding: utf-8 -*-
"""Client side of the virtual server: connect, log in, read and post.

Connections go over ssh (`ssh USER@HOST agentipc bridge client`) or directly
over TCP to a node that allows this address. Encrypted servers are handled
here: the key arrives wrapped under the password-derived key, and message
bodies are encrypted and decrypted on this side only.
"""

import base64
import json
import os
import socket
import subprocess

from agentipc import auth, crypto, files, proto


class ClientError(Exception):
    pass


class Connection(object):
    def __init__(self, rfile, wfile, closer=None):
        self.rfile = rfile
        self.wfile = wfile
        self.closer = closer

    def send(self, line):
        self.wfile.write((line + "\n").encode("utf-8"))
        self.wfile.flush()

    def readline(self):
        raw = self.rfile.readline(proto.MAX_LINE)
        if not raw:
            raise ClientError("connection closed")
        return raw.decode("utf-8", "replace").rstrip("\r\n")

    def close(self):
        if self.closer:
            self.closer()
            self.closer = None


def open_ssh(target, port=22, key=None, command="agentipc bridge client"):
    argv = ["ssh", "-o", "BatchMode=yes", "-p", str(port)]
    if key:
        argv += ["-i", key]
    argv += [target, command]
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def close():
        if proc.poll() is None:
            proc.kill()
        proc.wait()

    return Connection(proc.stdout, proc.stdin, closer=close)


def open_direct(host, port):
    sock = socket.create_connection((host, port), timeout=10)
    sock.settimeout(None)
    rfile = sock.makefile("rb")
    wfile = sock.makefile("wb")
    wfile.write(b"MODE client\n")
    wfile.flush()

    def close():
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except socket.error:
            pass
        sock.close()

    return Connection(rfile, wfile, closer=close)


def _chan(name):
    return name.lstrip("#").lower()


class Session(object):
    """A logged-in connection to one virtual server."""

    def __init__(self, conn, user, server, encrypted, key, token):
        self.conn = conn
        self.user = user
        self.server = server
        self.encrypted = encrypted
        self.key = key            # server key, bytes, or None
        self.token = token

    def join(self, chan):
        """Join and return the history messages."""
        chan = _chan(chan)
        self.conn.send("JOIN #%s" % chan)
        messages = []
        while True:
            line = self.conn.readline()
            if line.startswith("ERR"):
                raise ClientError(line)
            if line.startswith("MSG "):
                messages.append(self._decode(json.loads(line[4:])))
            elif line == "ENDHIST #%s" % chan:
                return messages

    def say(self, chan, text):
        """Post a message. Returns (sequence number, live messages seen while waiting)."""
        chan = _chan(chan)
        if "\n" in text or "\r" in text:
            raise ClientError("message must be one line")
        body = crypto.encrypt(self.key, text) if self.encrypted else text
        self.conn.send("SAY #%s %s" % (chan, body))
        live = []
        while True:
            line = self.conn.readline()
            if line.startswith("ERR"):
                raise ClientError(line)
            if line.startswith("SENT "):
                return int(line.split()[1]), live
            if line.startswith("MSG "):
                live.append(self._decode(json.loads(line[4:])))

    def names(self, chan):
        chan = _chan(chan)
        self.conn.send("NAMES #%s" % chan)
        found = []
        while True:
            line = self.conn.readline()
            if line.startswith("ERR"):
                raise ClientError(line)
            if line.startswith("NAME "):
                found.append(json.loads(line[5:]))
            elif line == "ENDNAMES #%s" % chan:
                return found

    def nodes(self):
        self.conn.send("NODES")
        found = []
        while True:
            line = self.conn.readline()
            if line.startswith("NODE "):
                found.append(json.loads(line[5:]))
            elif line == "ENDNODES":
                return found

    def send_file(self, node_id, path, transfer_id=None):
        """Upload a local file to a node through the mesh. Plain servers only."""
        if self.encrypted:
            raise ClientError("file transfer is not supported on encrypted servers yet")
        size, total, digest = files.file_info(path)
        tid = transfer_id or proto.new_id()
        name = os.path.basename(path)
        self.conn.send("SENDFILE %s %s %d %d %s %s" % (node_id, name, size, total, digest, tid))
        for index, chunk in enumerate(files.read_chunks(path)):
            self.conn.send("FILECHUNK %d %s" % (index, base64.b64encode(chunk).decode("ascii")))
        line = self.conn.readline()
        if line.startswith("ERR"):
            raise ClientError(line)
        return line.split()[1]

    def next_message(self):
        """Block for the next line; returns a decoded live message, or None for other lines."""
        line = self.conn.readline()
        if line.startswith("MSG "):
            return self._decode(json.loads(line[4:]))
        return None

    def quit(self):
        try:
            self.conn.send("QUIT")
        except (IOError, OSError, ValueError):
            pass
        self.conn.close()

    def _decode(self, rec):
        if self.encrypted:
            try:
                rec["body"] = crypto.decrypt(self.key, rec["body"]).decode("utf-8", "replace")
            except crypto.CryptoError:
                rec["body"] = "<undecryptable>"
        return rec


def login(conn, user, server, password):
    """Password login. Returns a Session; encrypted servers get their key here."""
    conn.send("LOGIN %s %s" % (user, server))
    line = conn.readline()
    if line.startswith("ERR"):
        raise ClientError(line)
    _, salt, iters, nonce = line.split()
    key = auth.derive_key(password, auth.unhexlify(salt), int(iters))
    conn.send("PROOF " + auth.proof_for(key, nonce))
    line = conn.readline()
    if line.startswith("ERR"):
        raise ClientError(line)
    _, token, srv, enc = line.split()
    server_key = None
    if enc == "1":
        wrapped = conn.readline()
        if not wrapped.startswith("KEY "):
            raise ClientError("missing server key")
        server_key = crypto.decrypt(key, wrapped[4:])
    return Session(conn, user, srv, enc == "1", server_key, token)


def login_token(conn, token):
    """Identity-only login with a token. Refused for encrypted servers."""
    conn.send("TOKEN " + token)
    line = conn.readline()
    if line.startswith("ERR"):
        raise ClientError(line)
    _, tok, srv, enc = line.split()
    return Session(conn, None, srv, enc == "1", None, tok)
