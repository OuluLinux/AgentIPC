# -*- coding: utf-8 -*-
"""Wire protocol version 1: framing, link frames and routed packets."""

import binascii
import json
import os
import re

# Wire versions. A node speaks versions MIN_PROTOCOL_VERSION..PROTOCOL_VERSION.
# A link runs at the highest version both sides speak. See negotiate().
PROTOCOL_VERSION = 2
MIN_PROTOCOL_VERSION = 2
# What this node can do. A node that lacks a feature (a small C node has no
# virtual server) says so, and the other side can avoid sending it those kinds.
FEATURES = ["core", "vs", "shell", "file"]
# Unicast kinds that only a node with a feature can handle. Any other kind needs "core".
KIND_FEATURES = {
    "exec": "shell", "exec-result": "shell",
    "file": "file",
    "login-start": "vs", "login-proof": "vs", "verify": "vs",
    "sync": "vs", "history": "vs", "membership": "vs", "channel": "vs",
}
MAX_LINE = 1 << 20
DEFAULT_TTL = 16
MAX_TTL = 64
MAX_MULTICAST = 64
FLOOD = "*"
NODE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
FRAME_TYPES = ("hello", "bye", "packet")

try:
    string_types = basestring  # noqa: F821 (Python 2)
except NameError:
    string_types = str


class ProtoError(Exception):
    """Carries the bye reason to send before closing the link."""


def new_id():
    """16 random hex characters."""
    return binascii.hexlify(os.urandom(8)).decode("ascii")


def check_node(name):
    if not isinstance(name, string_types) or not NODE_RE.match(name):
        raise ProtoError("bad-frame")
    return name


def check_dst(dst):
    """dst is FLOOD, one node id, or a list of 1..MAX_MULTICAST distinct node ids."""
    if isinstance(dst, list):
        for name in dst:
            check_node(name)
        if not 1 <= len(dst) <= MAX_MULTICAST or len(set(dst)) != len(dst):
            raise ProtoError("bad-frame")
    elif dst != FLOOD:
        check_node(dst)
    return dst


def destinations(dst):
    """The node ids a packet is addressed to. FLOOD is not a node id; callers check for it first."""
    return list(dst) if isinstance(dst, list) else [dst]


def encode(frame):
    return (json.dumps(frame, sort_keys=True) + "\n").encode("ascii")


def decode(line):
    """Parse one line. Raises ProtoError('bad-frame') on anything malformed."""
    if len(line) > MAX_LINE:
        raise ProtoError("bad-frame")
    try:
        frame = json.loads(line.decode("utf-8"))
    except ValueError:
        raise ProtoError("bad-frame")
    if not isinstance(frame, dict) or frame.get("type") not in FRAME_TYPES:
        raise ProtoError("bad-frame")
    kind = frame["type"]
    if kind == "hello":
        _require(frame, "node", "version", "link")
        check_node(frame["node"])
        if not isinstance(frame["version"], int):
            raise ProtoError("bad-frame")
        if "min_version" in frame and not isinstance(frame["min_version"], int):
            raise ProtoError("bad-frame")
        if "features" in frame and not (isinstance(frame["features"], list) and
                                        all(isinstance(f, string_types) for f in frame["features"])):
            raise ProtoError("bad-frame")
    elif kind == "bye":
        _require(frame, "reason")
    else:
        _require(frame, "src", "dst", "id", "ttl", "kind", "payload")
        check_node(frame["src"])
        check_dst(frame["dst"])
        if not isinstance(frame["ttl"], int) or not 1 <= frame["ttl"] <= MAX_TTL:
            raise ProtoError("bad-frame")
        if not isinstance(frame["payload"], dict) or not isinstance(frame["kind"], string_types):
            raise ProtoError("bad-frame")
    return frame


def _require(frame, *keys):
    for key in keys:
        if key not in frame:
            raise ProtoError("bad-frame")


def hello(node, link, features=None):
    return {"type": "hello", "node": node, "version": PROTOCOL_VERSION,
            "min_version": MIN_PROTOCOL_VERSION, "link": link,
            "features": list(FEATURES if features is None else features)}


def needs_feature(kind):
    return KIND_FEATURES.get(kind, "core")


def negotiate(peer_version, peer_min):
    """The wire version for a link, or None if the two ranges do not overlap."""
    top = min(peer_version, PROTOCOL_VERSION)
    floor = max(peer_min, MIN_PROTOCOL_VERSION)
    return top if top >= floor else None


def bye(reason):
    return {"type": "bye", "reason": reason}


def packet(src, dst, kind, payload, ttl=DEFAULT_TTL, packet_id=None):
    return {"type": "packet", "src": src, "dst": dst, "id": packet_id or new_id(),
            "ttl": ttl, "kind": kind, "payload": payload}
