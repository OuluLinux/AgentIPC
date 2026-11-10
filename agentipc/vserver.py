# -*- coding: utf-8 -*-
"""The virtual server: IRC-like channels that live on the mesh.

Every node stores the channel records it has seen and gossips membership.
Clients connect to one node (over ssh or direct TCP) and speak a line
protocol. Encrypted servers carry ciphertext bodies: the node never needs
the key, because the client encrypts and decrypts.

Client protocol, one command per line. Replies are lines too:

    LOGIN USER SERVER        -> CHALLENGE SALT ITERS NONCE
    PROOF HEX                -> OK TOKEN SERVER ENCRYPTED  [KEY WRAPPED]   or ERR REASON
    TOKEN TOKEN              -> OK TOKEN SERVER ENCRYPTED                  or ERR REASON
    JOIN #CHAN               -> JOINED #CHAN, MSG... (history), ENDHIST #CHAN
    PART #CHAN               -> PARTED #CHAN
    SAY #CHAN BODY           -> SENT OSEQ  (BODY is ciphertext on encrypted servers)
    NAMES #CHAN              -> NAME {json}..., ENDNAMES #CHAN
    NODES                    -> NODE {json}..., ENDNODES
    SENDFILE NODE NAME SIZE TOTAL SHA256 ID
                             -> (then FILECHUNK INDEX BASE64 for each chunk)
                                FILESENT ID  or ERR REASON
    VERSION N                -> VERSION M  (M = highest common client version; optional, default 1)
    EXEC NODE TIMEOUT CMD    -> EXECRESULT {json}  or ERR REASON
                                (runs only where that node enables shell commands)
    QUIT                     -> BYE

Live messages arrive as MSG {json} with "kind": "live".
"""

import json
import os
import threading

from agentipc import auth, files, proto
from agentipc.store import utcnow

try:
    import queue
except ImportError:  # Python 2
    import Queue as queue

MAX_BODY = 4096
HISTORY_CHUNK = 50
LOGIN_TIMEOUT = 20.0
CLIENT_PROTOCOL_VERSION = 1


class RecordStore(object):
    """Channel records from every origin, kept in an append-only JSON-lines file."""

    def __init__(self, path):
        self.path = path
        self.records = []
        self.keys = set()
        if path and os.path.exists(path):
            with open(path) as fh:
                for line in fh:
                    if line.strip():
                        self._remember(json.loads(line))

    def _remember(self, rec):
        self.keys.add((rec["origin"], rec["oseq"]))
        self.records.append(rec)

    def add(self, rec):
        """Returns True if the record was new."""
        if (rec["origin"], rec["oseq"]) in self.keys:
            return False
        self._remember(rec)
        if self.path:
            directory = os.path.dirname(self.path)
            if directory and not os.path.isdir(directory):
                os.makedirs(directory)
            with open(self.path, "a") as fh:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
        return True

    def channel(self, server, chan):
        found = [r for r in self.records if r["server"] == server and r["chan"] == chan]
        return sorted(found, key=lambda r: (r["ts"], r["origin"], r["oseq"]))

    def max_seq(self):
        best = {}
        for rec in self.records:
            best[rec["origin"]] = max(best.get(rec["origin"], 0), rec["oseq"])
        return best

    def after(self, have):
        """Records the holder of `have` (origin -> highest oseq) is missing."""
        return [r for r in self.records if r["oseq"] > have.get(r["origin"], 0)]


def valid_record(rec):
    try:
        proto.check_node(rec["origin"])
        proto.check_node(rec["server"])
        proto.check_node(rec["chan"])
        proto.check_node(rec["user"])
        return (isinstance(rec["oseq"], int) and rec["oseq"] > 0
                and isinstance(rec["body"], proto.string_types)
                and len(rec["body"]) <= 2 * MAX_BODY
                and isinstance(rec["ts"], proto.string_types))
    except (KeyError, proto.ProtoError, TypeError):
        return False


def _str(payload, *keys):
    for key in keys:
        if not isinstance(payload.get(key), proto.string_types):
            return False
    return True


class VirtualServer(object):
    def __init__(self, node, records_path=None):
        self.node = node
        self.records = RecordStore(records_path)
        self.members = set()      # (server, chan, user, node)
        self.sessions = set()
        node.register("membership", self._h_membership)
        node.register("channel", self._h_channel)
        node.register("sync", self._h_sync)
        node.register("history", self._h_history)
        node.add_service(self)

    # ----- gossip ------------------------------------------------------

    def on_link_up(self, link):
        mine = [[s, c, u] for (s, c, u, n) in self.members if n == self.node.id]
        self.node.send(proto.FLOOD, "sync", {"have": self.records.max_seq(), "members": mine})

    def on_node_gone(self, node_id):
        self.members = set(m for m in self.members if m[3] != node_id)

    def _h_membership(self, pkt):
        payload = pkt["payload"]
        if not _str(payload, "server", "chan", "user", "action"):
            return
        member = (payload["server"], payload["chan"], payload["user"], pkt["src"])
        if payload["action"] == "join":
            self.members.add(member)
        elif payload["action"] == "part":
            self.members.discard(member)

    def _h_channel(self, pkt):
        rec = pkt["payload"]
        if not valid_record(rec) or rec["origin"] != pkt["src"]:
            return
        if self.records.add(rec):
            self._deliver(rec)

    def _h_sync(self, pkt):
        origin = pkt["src"]
        if origin == self.node.id:
            return
        payload = pkt["payload"]
        self.members = set(m for m in self.members if m[3] != origin)
        for item in payload.get("members", []):
            if isinstance(item, list) and len(item) == 3 and all(isinstance(x, proto.string_types) for x in item):
                self.members.add((item[0], item[1], item[2], origin))
        have = payload.get("have")
        if not isinstance(have, dict):
            return
        missing = self.records.after(have)
        for start in range(0, len(missing), HISTORY_CHUNK):
            self.node.send(origin, "history", {"records": missing[start:start + HISTORY_CHUNK]})

    def _h_history(self, pkt):
        for rec in pkt["payload"].get("records", []):
            if valid_record(rec) and self.records.add(rec):
                self._deliver(rec)

    # ----- local sessions ----------------------------------------------

    def attach(self, session):
        self.sessions.add(session)

    def session_closed(self, session):
        for chan in list(session.joined):
            self.part(session, chan)
        self.sessions.discard(session)

    def join(self, session, chan):
        session.joined.add(chan)
        self.members.add((session.server, chan, session.user, self.node.id))
        self.node.send(proto.FLOOD, "membership", {
            "server": session.server, "chan": chan, "user": session.user, "action": "join"})
        session.out("JOINED #%s" % chan)
        for rec in self.records.channel(session.server, chan):
            session.out("MSG " + _json(dict(rec, kind="hist")))
        session.out("ENDHIST #%s" % chan)

    def part(self, session, chan):
        session.joined.discard(chan)
        self.members.discard((session.server, chan, session.user, self.node.id))
        self.node.send(proto.FLOOD, "membership", {
            "server": session.server, "chan": chan, "user": session.user, "action": "part"})

    def say(self, session, chan, body):
        rec = {"origin": self.node.id, "oseq": self.node.next_oseq(), "server": session.server,
               "chan": chan, "user": session.user, "body": body, "ts": utcnow()}
        self.records.add(rec)
        self._deliver(rec)
        self.node.send(proto.FLOOD, "channel", rec)
        return rec["oseq"]

    def names(self, server, chan):
        return sorted((u, n) for (s, c, u, n) in self.members if s == server and c == chan)

    def _deliver(self, rec):
        line = "MSG " + _json(dict(rec, kind="live"))
        for session in self.sessions:
            if session.server == rec["server"] and rec["chan"] in session.joined:
                session.out(line)


def _json(obj):
    return json.dumps(obj, sort_keys=True)


class ClientSession(object):
    """One client connection: reads commands, writes replies from a queue."""

    def __init__(self, vs, rfile, wfile):
        self.vs = vs
        self.node = vs.node
        self.rfile = rfile
        self.wfile = wfile
        self.user = None
        self.server = None
        self.token = None
        self.encrypted = False
        self.joined = set()
        self._login = None        # (user, server, req) while a login is in progress
        self._upload = None       # transfer being received from this client
        self._out = queue.Queue()
        self._closing = False

    def out(self, line):
        self._out.put(line)

    def run(self):
        writer = threading.Thread(target=self._write_loop, name="client-write")
        writer.daemon = True
        writer.start()
        with self.node.lock:
            self.vs.attach(self)
        try:
            while not self._closing:
                raw = self.rfile.readline(proto.MAX_LINE)
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                with self.node.lock:
                    self._command(line)
        except (IOError, OSError, ValueError):
            pass
        finally:
            with self.node.lock:
                self.vs.session_closed(self)
            self._out.put(None)

    def _write_loop(self):
        while True:
            line = self._out.get()
            if line is None:
                break
            try:
                self.wfile.write((line + "\n").encode("utf-8"))
                self.wfile.flush()
            except (IOError, OSError, ValueError):
                break

    def _command(self, line):
        verb, _, rest = line.partition(" ")
        verb = verb.upper()
        rest = rest.strip()
        handler = {
            "LOGIN": self._cmd_login, "PROOF": self._cmd_proof, "TOKEN": self._cmd_token,
            "EXEC": self._cmd_exec, "VERSION": self._cmd_version,
            "JOIN": self._cmd_join, "PART": self._cmd_part, "SAY": self._cmd_say,
            "NAMES": self._cmd_names, "NODES": self._cmd_nodes, "QUIT": self._cmd_quit,
            "SENDFILE": self._cmd_sendfile, "FILECHUNK": self._cmd_filechunk,
        }.get(verb)
        if handler is None:
            self.out("ERR unknown-command")
            return
        handler(rest)

    def _need_login(self):
        if self.user is None:
            self.out("ERR not-logged-in")
            return True
        return False

    def _channel(self, text):
        name = text.lstrip("#").lower()
        if not proto.NODE_RE.match(name):
            self.out("ERR bad-channel")
            return None
        return name

    # ----- login -------------------------------------------------------

    def _cmd_login(self, rest):
        parts = rest.split()
        if self.user is not None:
            self.out("ERR already-logged-in")
        elif len(parts) != 2 or not all(proto.NODE_RE.match(p) for p in parts):
            self.out("ERR usage: LOGIN USER SERVER")
        elif self.node.authority is None:
            self.out("ERR no-authority")
        else:
            user, server = parts
            sent = self.node.request(self.node.authority, "login-start", {"user": user, "server": server},
                                     lambda payload: self._on_challenge(user, server, payload),
                                     timeout=LOGIN_TIMEOUT)
            if not sent:
                self.out("ERR no-route")

    def _on_challenge(self, user, server, payload):
        if not payload.get("ok", True) or "salt" not in payload:
            self.out("ERR %s" % payload.get("reason", payload.get("error", "login-failed")))
            return
        self._login = (user, server, payload["req"])
        self.out("CHALLENGE %s %d %s" % (payload["salt"], payload["iters"], payload["nonce"]))

    def _cmd_proof(self, rest):
        if self._login is None:
            self.out("ERR no-login-in-progress")
            return
        user, server, req = self._login
        self._login = None
        self.node.requests[req] = lambda payload: self._on_result(user, payload)
        self.node.watch(req, LOGIN_TIMEOUT)
        self.node.send(self.node.authority, "login-proof", {"req": req, "proof": rest})

    def _on_result(self, user, payload):
        if not payload.get("ok"):
            self.out("ERR %s" % payload.get("reason", payload.get("error", "login-failed")))
            return
        self.user = user
        self.server = payload["server"]
        self.token = payload["token"]
        self.encrypted = bool(payload["encrypted"])
        self.out("OK %s %s %d" % (payload["token"], self.server, 1 if self.encrypted else 0))
        if payload.get("wrapped"):
            self.out("KEY %s" % payload["wrapped"])

    def _cmd_token(self, rest):
        if self.user is not None:
            self.out("ERR already-logged-in")
            return

        def done(info):
            if info is None:
                self.out("ERR invalid-token")
            elif info["encrypted"]:
                self.out("ERR encrypted-server-needs-password")
            else:
                self.user, self.server, self.encrypted = info["user"], info["server"], False
                self.token = rest
                self.out("OK %s %s 0" % (rest, self.server))

        auth.verify_remote(self.node, rest, done)

    # ----- channels ----------------------------------------------------

    def _cmd_join(self, rest):
        chan = self._channel(rest)
        if chan is None or self._need_login():
            return
        if chan in self.joined:
            self.out("ERR already-joined")
            return
        self.vs.join(self, chan)

    def _cmd_part(self, rest):
        chan = self._channel(rest)
        if chan is None or self._need_login():
            return
        if chan not in self.joined:
            self.out("ERR not-joined")
            return
        self.vs.part(self, chan)
        self.out("PARTED #%s" % chan)

    def _cmd_say(self, rest):
        head, _, body = rest.partition(" ")
        chan = self._channel(head)
        if chan is None or self._need_login():
            return
        if chan not in self.joined:
            self.out("ERR not-joined")
            return
        if not body or len(body) > MAX_BODY:
            self.out("ERR bad-body")
            return
        self.out("SENT %d" % self.vs.say(self, chan, body))

    def _cmd_names(self, rest):
        chan = self._channel(rest)
        if chan is None or self._need_login():
            return
        for user, node_id in self.vs.names(self.server, chan):
            self.out("NAME " + _json({"user": user, "node": node_id}))
        self.out("ENDNAMES #%s" % chan)

    def _cmd_version(self, rest):
        if not rest.isdigit() or int(rest) < 1:
            self.out("ERR version")
            return
        self.out("VERSION %d" % min(int(rest), CLIENT_PROTOCOL_VERSION))

    def _cmd_nodes(self, rest):
        for row in self.node.status():
            self.out("NODE " + _json(row))
        self.out("ENDNODES")

    def _cmd_quit(self, rest):
        self.out("BYE")
        self._closing = True

    # ----- shell -------------------------------------------------------

    def _cmd_exec(self, rest):
        parts = rest.split(" ", 2)
        if self._need_login():
            return
        if self.encrypted:
            self.out("ERR encrypted-server-has-no-exec")
            return
        if len(parts) != 3 or not proto.NODE_RE.match(parts[0]) or not parts[1].isdigit():
            self.out("ERR usage: EXEC NODE TIMEOUT COMMAND")
            return
        node_id, timeout, command = parts[0], int(parts[1]), parts[2]

        def done(payload):
            if "error" in payload:
                self.out("ERR %s" % payload["error"])
            else:
                self.out("EXECRESULT " + _json(payload))

        # Allow the command's own timeout plus room for the trip over the mesh.
        sent = self.node.request(node_id, "exec", {
            "token": self.token, "command": command, "timeout": timeout}, done,
            timeout=timeout + 15)
        if not sent:
            self.out("ERR no-route")

    # ----- files -------------------------------------------------------

    def _cmd_sendfile(self, rest):
        parts = rest.split()
        if self._need_login():
            return
        if self._upload is not None:
            self.out("ERR upload-in-progress")
            return
        if self.encrypted:
            self.out("ERR encrypted-server-has-no-file-transfer")
            return
        if len(parts) != 6 or not proto.NODE_RE.match(parts[0]) or not proto.NODE_RE.match(parts[1]):
            self.out("ERR usage: SENDFILE NODE NAME SIZE TOTAL SHA256 ID")
            return
        dst, name, size, total, sha, tid = parts
        if not (size.isdigit() and total.isdigit() and int(total) > 0 and
                len(sha) == 64 and tid.isalnum() and len(tid) <= 32):
            self.out("ERR bad-arguments")
            return
        if int(size) > files.MAX_FILE_SIZE:
            self.out("ERR too-large")
            return
        self._upload = {"dst": dst, "name": name, "size": int(size), "total": int(total),
                        "sha256": sha, "id": tid, "next": 0}

    def _cmd_filechunk(self, rest):
        upload = self._upload
        if upload is None:
            self.out("ERR no-upload")
            return
        index_text, _, data = rest.partition(" ")
        if not index_text.isdigit() or int(index_text) != upload["next"] or not data:
            self._upload = None
            self.out("ERR bad-chunk")
            return
        index = int(index_text)
        sent = self.node.send(upload["dst"], "file", {
            "id": upload["id"], "name": upload["name"], "index": index, "total": upload["total"],
            "size": upload["size"], "sha256": upload["sha256"], "data": data, "user": self.user})
        if not sent:
            self._upload = None
            self.out("ERR no-route")
            return
        upload["next"] += 1
        if upload["next"] == upload["total"]:
            self.out("FILESENT %s" % upload["id"])
            self._upload = None

