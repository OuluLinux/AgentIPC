# -*- coding: utf-8 -*-
"""Encrypted direct links, using PyNaCl. See DESIGN.md, "Link encryption".

A direct link to a peer that has a "pubkey" in config runs this handshake first:

    dialer    -> kx  {node, eph, sig}   sig = Ed25519(init label + eph)
    responder -> kx  {node, eph, sig}   sig = Ed25519(resp label + eph + init eph)

Each side checks the other's signature against the public key configured for it,
and both derive one session key from the X25519 exchange. Every later line is
sealed with SecretBox (JSON {"enc": base64}), so the link code above still sees
whole lines.

PyNaCl is imported only here, so nodes without it still run plain links.
"""

import base64
import binascii
import json
import os

from agentipc import proto

IDENTITY_FILE = "identity.key"
KX_LIMIT = 4096
SEALED_LIMIT = 4 * proto.MAX_LINE
INIT_LABEL = b"agentipc-link-init|"
RESP_LABEL = b"agentipc-link-resp|"


class LinkCryptoError(ValueError):
    """A failed handshake or a sealed line that does not open. The link is dropped."""


def _pynacl():
    try:
        import nacl.exceptions  # noqa: F401
        import nacl.public  # noqa: F401
        import nacl.secret  # noqa: F401
        import nacl.signing  # noqa: F401
        import nacl
    except ImportError:
        raise LinkCryptoError("encrypted links need PyNaCl (pip install pynacl)")
    return nacl


def _hex(data):
    return binascii.hexlify(data).decode("ascii")


def _unhex(text, length):
    try:
        data = binascii.unhexlify(text.encode("ascii"))
    except (AttributeError, TypeError, ValueError, binascii.Error):
        raise LinkCryptoError("bad hex in handshake")
    if len(data) != length:
        raise LinkCryptoError("bad length in handshake")
    return data


def identity(home):
    """This node's Ed25519 signing key. Created on first use, readable by its owner only."""
    nacl = _pynacl()
    path = os.path.join(home, IDENTITY_FILE)
    if not os.path.exists(path):
        if not os.path.isdir(home):
            os.makedirs(home)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(_hex(os.urandom(32)) + "\n")
    with open(path) as fh:
        return nacl.signing.SigningKey(_unhex(fh.read().strip(), 32))


def public_key_hex(signing_key):
    """The text to put in config as a peer's "pubkey" (or in a listener's "trusted")."""
    return _hex(bytes(signing_key.verify_key))


def _send(wfile, obj):
    wfile.write((json.dumps(obj, sort_keys=True) + "\n").encode("ascii"))
    wfile.flush()


def _read_kx(rfile):
    line = rfile.readline(KX_LIMIT)
    if not line:
        raise LinkCryptoError("connection closed during handshake")
    try:
        frame = json.loads(line.decode("ascii"))
    except ValueError:
        raise LinkCryptoError("bad handshake frame")
    if not isinstance(frame, dict) or frame.get("type") != "kx":
        raise LinkCryptoError("expected a key exchange frame")
    if not frame.get("node") or not isinstance(frame["node"], type(u"")):
        raise LinkCryptoError("handshake has no node name")
    return frame


def dial(rfile, wfile, my_name, my_key, peer_pubkey):
    """Initiator. Returns (Sealed streams, the peer's node name) after checking its key."""
    nacl = _pynacl()
    eph = nacl.public.PrivateKey.generate()
    eph_pub = bytes(eph.public_key)
    sig = my_key.sign(INIT_LABEL + eph_pub).signature
    _send(wfile, {"type": "kx", "node": my_name, "eph": _hex(eph_pub), "sig": _hex(sig)})
    reply = _read_kx(rfile)
    resp_pub = _unhex(reply["eph"], 32)
    peer = nacl.signing.VerifyKey(_unhex(peer_pubkey, 32))
    try:
        peer.verify(RESP_LABEL + resp_pub + eph_pub, _unhex(reply["sig"], 64))
    except nacl.exceptions.BadSignatureError:
        raise LinkCryptoError("peer does not hold the key configured for it")
    shared = nacl.public.Box(eph, nacl.public.PublicKey(resp_pub)).shared_key()
    return Sealed(rfile, wfile, shared), reply["node"]


def accept(rfile, wfile, my_name, my_key, trusted):
    """Responder. trusted maps node name to public key hex. Returns (Sealed streams, peer name)."""
    nacl = _pynacl()
    first = _read_kx(rfile)
    name = first["node"]
    if name not in trusted:
        raise LinkCryptoError("untrusted node %s" % name)
    init_pub = _unhex(first["eph"], 32)
    try:
        nacl.signing.VerifyKey(_unhex(trusted[name], 32)).verify(
            INIT_LABEL + init_pub, _unhex(first["sig"], 64))
    except nacl.exceptions.BadSignatureError:
        raise LinkCryptoError("node %s does not hold its configured key" % name)
    eph = nacl.public.PrivateKey.generate()
    eph_pub = bytes(eph.public_key)
    sig = my_key.sign(RESP_LABEL + eph_pub + init_pub).signature
    _send(wfile, {"type": "kx", "node": my_name, "eph": _hex(eph_pub), "sig": _hex(sig)})
    shared = nacl.public.Box(eph, nacl.public.PublicKey(init_pub)).shared_key()
    return Sealed(rfile, wfile, shared), name


class Sealed(object):
    """Binary streams whose lines are sealed with SecretBox. Same readline/write/flush/close
    surface as the plain streams, so Link needs no change."""

    def __init__(self, rfile, wfile, key):
        self.rfile = rfile
        self.wfile = wfile
        self.box = _pynacl().secret.SecretBox(key)

    def readline(self, limit=-1):
        line = self.rfile.readline(SEALED_LIMIT)
        if not line:
            return b""
        try:
            sealed = base64.b64decode(json.loads(line.decode("ascii"))["enc"].encode("ascii"))
            plain = bytes(self.box.decrypt(sealed))
        except Exception:
            raise LinkCryptoError("sealed line does not open")
        if 0 < limit < len(plain):
            raise LinkCryptoError("sealed line is too long")
        return plain

    def write(self, data):
        for line in data.splitlines(True):
            if not line.endswith(b"\n"):
                raise LinkCryptoError("only whole lines can be sealed")
            sealed = bytes(self.box.encrypt(line))
            text = base64.b64encode(sealed).decode("ascii")
            self.wfile.write((json.dumps({"enc": text}, sort_keys=True) + "\n").encode("ascii"))

    def flush(self):
        self.wfile.flush()

    def close(self):
        for stream in (self.wfile, self.rfile):
            try:
                stream.close()
            except (IOError, OSError, ValueError):
                pass


def load_identity_or_none(home):
    """identity() when PyNaCl is installed, else None. Used by daemon to decide whether a
    node can offer encrypted links at all."""
    try:
        return identity(home)
    except LinkCryptoError:
        return None
