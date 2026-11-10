# -*- coding: utf-8 -*-
"""Wire protocol version 1: framing, link frames and routed packets."""

import binascii
import json
import os
import re

PROTOCOL_VERSION = 1
MAX_LINE = 1 << 20
DEFAULT_TTL = 16
MAX_TTL = 64
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
    elif kind == "bye":
        _require(frame, "reason")
    else:
        _require(frame, "src", "dst", "id", "ttl", "kind", "payload")
        check_node(frame["src"])
        if frame["dst"] != FLOOD:
            check_node(frame["dst"])
        if not isinstance(frame["ttl"], int) or not 1 <= frame["ttl"] <= MAX_TTL:
            raise ProtoError("bad-frame")
        if not isinstance(frame["payload"], dict) or not isinstance(frame["kind"], string_types):
            raise ProtoError("bad-frame")
    return frame


def _require(frame, *keys):
    for key in keys:
        if key not in frame:
            raise ProtoError("bad-frame")


def hello(node, link):
    return {"type": "hello", "node": node, "version": PROTOCOL_VERSION, "link": link}


def bye(reason):
    return {"type": "bye", "reason": reason}


def packet(src, dst, kind, payload, ttl=DEFAULT_TTL, packet_id=None):
    return {"type": "packet", "src": src, "dst": dst, "id": packet_id or new_id(),
            "ttl": ttl, "kind": kind, "payload": payload}
