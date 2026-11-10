# -*- coding: utf-8 -*-
"""File transfer over the mesh, started from a virtual-server session.

The sender splits the file into chunks. Each chunk travels as a routed packet
of kind "file" addressed to the receiving node. Chunks may arrive out of
order, so each one is written at its own offset. When every chunk has
arrived, the whole file is checked against the SHA-256 the sender declared,
then moved into the inbox.

Encrypted virtual servers do not support file transfer yet.
"""

import base64
import hashlib
import os
import re
import threading

from agentipc import proto

CHUNK_SIZE = 32 * 1024
MAX_FILE_SIZE = 256 * 1024 * 1024
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class FileService(object):
    def __init__(self, node, inbox):
        self.node = node
        self.inbox = inbox
        self.partial = {}       # (sender, transfer id) -> state
        self.lock = threading.Lock()
        node.register("file", self._h_file)
        node.add_service(self)

    def on_link_up(self, link):
        pass

    def on_node_gone(self, node_id):
        pass

    def _h_file(self, pkt):
        p = pkt["payload"]
        sender, tid = pkt["src"], p.get("id")
        if not (isinstance(tid, proto.string_types) and NAME_RE.match(p.get("name", "")) and
                isinstance(p.get("index"), int) and isinstance(p.get("total"), int) and
                isinstance(p.get("size"), int) and isinstance(p.get("sha256"), proto.string_types) and
                isinstance(p.get("data"), proto.string_types) and 0 < p["total"] and
                0 <= p["index"] < p["total"] and 0 <= p["size"] <= MAX_FILE_SIZE):
            return
        try:
            data = base64.b64decode(p["data"].encode("ascii"))
        except (TypeError, ValueError):
            return
        key = (sender, tid)
        directory = os.path.join(self.inbox, sender)
        if not os.path.isdir(directory):
            os.makedirs(directory)
        temp = os.path.join(directory, ".partial-" + tid)
        state = self.partial.setdefault(key, {"got": set(), "total": p["total"], "name": p["name"],
                                              "size": p["size"], "sha256": p["sha256"]})
        if not os.path.exists(temp):
            open(temp, "wb").close()
        with open(temp, "r+b") as fh:
            fh.seek(p["index"] * CHUNK_SIZE)
            fh.write(data)
        state["got"].add(p["index"])
        if len(state["got"]) == state["total"]:
            self._finish(sender, tid, temp, state)

    def _finish(self, sender, tid, temp, state):
        del self.partial[(sender, tid)]
        with open(temp, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        if digest != state["sha256"] or os.path.getsize(temp) != state["size"]:
            os.remove(temp)
            return
        final = os.path.join(os.path.dirname(temp), state["name"])
        base, ext = os.path.splitext(final)
        counter = 1
        while os.path.exists(final):
            final = "%s.%d%s" % (base, counter, ext)
            counter += 1
        os.rename(temp, final)


def file_info(path):
    """Returns (size, total chunks, sha256 hex) for a local file."""
    size = os.path.getsize(path)
    total = max(1, (size + CHUNK_SIZE - 1) // CHUNK_SIZE)
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return size, total, digest.hexdigest()


def read_chunks(path):
    """Yields the file's chunks in order, as bytes."""
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK_SIZE), b""):
            yield chunk
