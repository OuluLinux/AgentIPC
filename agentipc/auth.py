# -*- coding: utf-8 -*-
"""Accounts, challenge-response login and session tokens.

The authority node stores accounts and per-server keys. Any node can serve a
login: the password never leaves the client. The client proves knowledge of
the password with an HMAC over a fresh nonce, keyed by a pbkdf2-derived key.
The authority stores that derived key, so it is password-equivalent and the
accounts file must stay private.

Tokens are HMAC-signed by the authority. Other nodes verify them by asking the
authority, and cache the answer until the token expires.

Failover (manual, see DESIGN.md "Authority failover"): the authority copies its
accounts to a standby over ssh links only. The standby keeps the copy as a
replica. `account promote` on the standby turns the replica into the accounts
with the next epoch. Nodes follow the highest epoch they hear in announces, so
the old authority steps down when it sees the new one.
"""

import binascii
import hashlib
import hmac
import json
import os
import time

from agentipc import crypto, proto

ITERATIONS = 100000
REPLICA_INTERVAL = 5.0   # seconds between checks for accounts to copy to the standby
REPLICA_FILE = "accounts.replica.json"
CHALLENGE_TTL = 60
VERIFY_TIMEOUT = 20.0    # seconds to wait for the authority to answer a token check
TOKEN_TTL = 3600
REPLY_KINDS = ("login-challenge", "login-result", "verify-result")


def hexlify(data):
    return binascii.hexlify(data).decode("ascii")


def unhexlify(text):
    return binascii.unhexlify(text.encode("ascii"))


def derive_key(password, salt, iterations):
    if not isinstance(password, bytes):
        password = password.encode("utf-8")
    return hashlib.pbkdf2_hmac("sha256", password, salt, iterations, 32)


def proof_for(key, nonce):
    return hmac.new(key, nonce.encode("ascii"), hashlib.sha256).hexdigest()


class Accounts(object):
    """Users and virtual servers, kept in one private JSON file."""

    def __init__(self, path):
        self.path = path
        self.data = {"secret": hexlify(os.urandom(32)), "users": {}, "servers": {},
                     "authority": None, "epoch": 0, "rev": 0}
        self._mtime = None
        self._refresh()

    def _refresh(self):
        """Reload when another process (the CLI) has changed the file."""
        if not os.path.exists(self.path):
            return
        mtime = os.path.getmtime(self.path)
        if mtime != self._mtime:
            with open(self.path) as fh:
                self.data = json.load(fh)
            # Files from before failover had no authority, epoch or rev.
            self.data.setdefault("authority", None)
            self.data.setdefault("epoch", 0)
            self.data.setdefault("rev", 0)
            self._mtime = mtime

    def save(self):
        self._refresh()
        self.data["rev"] = self.data.get("rev", 0) + 1
        write_private(self.path, self.data)
        self._mtime = os.path.getmtime(self.path)

    def add_user(self, user, password):
        proto.check_node(user)
        salt = os.urandom(16)
        key = derive_key(password, salt, ITERATIONS)
        self.data["users"][user] = {"salt": hexlify(salt), "iters": ITERATIONS, "key": hexlify(key)}
        self.save()

    def remove_user(self, user):
        if self.data["users"].pop(user, None) is None:
            raise KeyError(user)
        self.save()

    def user(self, user):
        self._refresh()
        return self.data["users"].get(user)

    def add_server(self, name, encrypted):
        proto.check_node(name)
        key = hexlify(crypto.new_key()) if encrypted else None
        self.data["servers"][name] = {"encrypted": bool(encrypted), "key": key}
        self.save()

    def server(self, name):
        self._refresh()
        return self.data["servers"].get(name)

    def secret(self):
        return unhexlify(self.data["secret"])

    def fake_salt(self, user):
        """Stable salt for unknown users, so login does not reveal who exists."""
        return hmac.new(self.secret(), ("salt:" + user).encode("utf-8"), hashlib.sha256).digest()[:16]


def write_private(path, data):
    """Write JSON atomically, readable by its owner only."""
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
    os.rename(tmp, path)


def read_replica(path):
    """The stored replica as {'authority', 'epoch', 'rev', 'data'}, or None."""
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return json.load(fh)


def promote(replica_path, accounts_path, node_id):
    """Make node_id the authority from the stored replica. Returns the new epoch.

    Refuses when this node already holds the accounts as authority. The new epoch
    is one more than the highest epoch in the replica or in the local accounts."""
    replica = read_replica(replica_path)
    if replica is None:
        raise ValueError("no replica to promote from")
    current = Accounts(accounts_path)
    if current.data.get("authority") == node_id:
        raise ValueError("this node is already the authority")
    data = dict(replica["data"])
    epoch = max(replica["epoch"], current.data.get("epoch", 0)) + 1
    data.update({"authority": node_id, "epoch": epoch})
    current.data = data
    current.save()
    return epoch


def make_token(accounts, user, server, expires):
    payload = ("%s|%s|%d" % (user, server, expires)).encode("utf-8")
    mac = hmac.new(accounts.secret(), payload, hashlib.sha256).hexdigest()
    return hexlify(payload) + "." + mac


def parse_token(accounts, token, now=None):
    """Returns {'user', 'server', 'expires'} or None if forged, malformed or expired."""
    try:
        body, mac = token.split(".")
        payload = unhexlify(body)
        expected = hmac.new(accounts.secret(), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, mac):
            return None
        user, server, expires = payload.decode("utf-8").split("|")
        expires = int(expires)
    except (ValueError, TypeError, binascii.Error):
        return None
    if expires < (now if now is not None else time.time()):
        return None
    return {"user": user, "server": server, "expires": expires}


class Authority(object):
    """Login, verify and replica handling. Every node has one; it answers
    login and verify only while it is the authority (it holds accounts and is the
    node the claim names). The others hold a replica and forward nothing."""

    def __init__(self, node, accounts=None, replica_path=None):
        self.node = node
        self.accounts = accounts
        self.replica_path = replica_path
        self.challenges = {}      # (requester, req) -> pending challenge
        self.sent = None          # (link, rev) of the last replica sent to the standby
        node.register("login-start", self._h_login_start)
        node.register("login-proof", self._h_login_proof)
        node.register("verify", self._h_verify)
        node.register_link("replica", self._h_replica)
        node.add_service(self)
        node.on_claim = self._on_claim

    def is_authority(self):
        return self.accounts is not None and self.node.is_authority()

    def _reply(self, dst, kind, req, body):
        body = dict(body, req=req)
        self.node.send(dst, kind, body)

    def _on_claim(self, name, epoch):
        """A higher claim names another authority: record it, so this node's CLI refuses writes."""
        if self.accounts is not None:
            self.accounts.data["authority"] = name
            self.accounts.data["epoch"] = epoch
            self.accounts.save()

    def _h_login_start(self, pkt):
        payload = pkt["payload"]
        req, user, server = payload.get("req"), payload.get("user"), payload.get("server")
        if not (req and isinstance(user, proto.string_types) and isinstance(server, proto.string_types)):
            return
        if not self.is_authority():
            self._reply(pkt["src"], "login-result", req, {"ok": False, "reason": "not-authority"})
            return
        if self.accounts.server(server) is None:
            self._reply(pkt["src"], "login-result", req, {"ok": False, "reason": "unknown-server"})
            return
        record = self.accounts.user(user)
        if record is None:
            salt, iters = self.accounts.fake_salt(user), ITERATIONS
        else:
            salt, iters = unhexlify(record["salt"]), record["iters"]
        nonce = proto.new_id() + proto.new_id()
        now = time.time()
        for key in [k for k, c in self.challenges.items() if now > c["until"]]:
            del self.challenges[key]
        self.challenges[(pkt["src"], req)] = {
            "user": user, "server": server, "nonce": nonce,
            "until": now + CHALLENGE_TTL}
        self._reply(pkt["src"], "login-challenge", req,
                    {"salt": hexlify(salt), "iters": iters, "nonce": nonce})

    def _h_login_proof(self, pkt):
        payload = pkt["payload"]
        req, proof = payload.get("req"), payload.get("proof")
        challenge = self.challenges.pop((pkt["src"], req), None)
        if not self.is_authority():
            self._reply(pkt["src"], "login-result", req, {"ok": False, "reason": "not-authority"})
            return
        if challenge is None or time.time() > challenge["until"]:
            self._reply(pkt["src"], "login-result", req, {"ok": False, "reason": "expired"})
            return
        record = self.accounts.user(challenge["user"])
        key = unhexlify(record["key"]) if record else None
        if key is None or not isinstance(proof, proto.string_types) or \
                not hmac.compare_digest(proof_for(key, challenge["nonce"]), proof):
            self._reply(pkt["src"], "login-result", req, {"ok": False, "reason": "bad-login"})
            return
        server = self.accounts.server(challenge["server"])
        if server is None:
            self._reply(pkt["src"], "login-result", req, {"ok": False, "reason": "unknown-server"})
            return
        wrapped = None
        if server["encrypted"]:
            try:
                wrapped = crypto.encrypt(key, unhexlify(server["key"]))
            except crypto.CryptoError as exc:
                self._reply(pkt["src"], "login-result", req, {"ok": False, "reason": str(exc)})
                return
        token = make_token(self.accounts, challenge["user"], challenge["server"],
                           int(time.time()) + TOKEN_TTL)
        self._reply(pkt["src"], "login-result", req, {
            "ok": True, "token": token, "server": challenge["server"],
            "encrypted": server["encrypted"], "wrapped": wrapped})

    def _h_verify(self, pkt):
        req = pkt["payload"].get("req")
        token = pkt["payload"].get("token")
        if not self.is_authority():
            self._reply(pkt["src"], "verify-result", req, {"ok": False})
            return
        info = parse_token(self.accounts, token) if isinstance(token, proto.string_types) else None
        if info is None or self.accounts.user(info["user"]) is None:
            self._reply(pkt["src"], "verify-result", req, {"ok": False})
            return
        server = self.accounts.server(info["server"]) or {"encrypted": False}
        self._reply(pkt["src"], "verify-result", req, {
            "ok": True, "user": info["user"], "server": info["server"],
            "encrypted": server["encrypted"], "expires": info["expires"]})

    def on_link_up(self, link):
        pass

    def on_node_gone(self, node_id):
        pass


    def replicate_once(self):
        """Send the accounts to the standby, if this node is the authority, the standby
        has an ssh link to us, and the accounts changed since the last send."""
        if not self.is_authority() or not self.node.standby:
            return
        with self.node.lock:
            link = self.node.ssh_link_to(self.node.standby)
            if link is None:
                return
            rev = self.accounts.data["rev"]
            if self.sent == (link, rev):
                return
            self.node.send_on(link, self.node.standby, "replica", {
                "authority": self.node.id, "epoch": self.accounts.data["epoch"],
                "rev": rev, "data": self.accounts.data})
            self.sent = (link, rev)

    def _h_replica(self, pkt, link):
        """Keep a copy of the authority's accounts. Only from its ssh link, and only newer ones."""
        payload = pkt["payload"]
        if self.is_authority() or link.kind != "ssh" or pkt["src"] != self.node.authority:
            return
        if payload.get("authority") != pkt["src"] or not isinstance(payload.get("data"), dict):
            return
        epoch, rev = payload.get("epoch"), payload.get("rev")
        if not isinstance(epoch, int) or not isinstance(rev, int):
            return
        stored = read_replica(self.replica_path) if self.replica_path else None
        if stored is not None and (epoch, rev) <= (stored["epoch"], stored["rev"]):
            return
        if self.replica_path:
            write_private(self.replica_path, {"authority": pkt["src"], "epoch": epoch, "rev": rev,
                                              "data": payload["data"]})


def install(node, accounts=None, replica_path=None):
    """Give a node the reply handlers, and the authority role if accounts is given."""
    node.token_cache = {}

    def reply(pkt):
        node.resolve(pkt["payload"])

    for kind in REPLY_KINDS:
        node.register(kind, reply)
    return Authority(node, accounts, replica_path)


def verify_remote(node, token, callback):
    """Call callback(info) with the token's details, or with None if it is not valid."""
    now = time.time()
    cached = node.token_cache.get(token)
    if cached is not None and cached["expires"] > now:
        callback(cached)
        return

    def done(payload):
        info = None
        if payload.get("ok"):
            info = {"user": payload["user"], "server": payload["server"],
                    "encrypted": payload["encrypted"], "expires": payload["expires"]}
            node.token_cache[token] = info
        callback(info)

    if not node.request(node.authority, "verify", {"token": token}, done, timeout=VERIFY_TIMEOUT):
        callback(None)
