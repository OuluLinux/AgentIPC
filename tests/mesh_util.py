# -*- coding: utf-8 -*-
"""Helpers for in-process mesh tests: nodes joined by socketpair links."""

import socket
import threading
import time

from agentipc.node import Node


def communicate(proc, timeout, data=None):
    """proc.communicate with a timeout. Python 2 has no timeout argument, so wait in a thread."""
    result = {}
    worker = threading.Thread(target=lambda: result.update(out=proc.communicate(data)))
    worker.daemon = True
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        proc.kill()
        worker.join()
        raise AssertionError("process did not finish within %s seconds" % timeout)
    return result["out"]


def connect(a, b, kind="local"):
    """Link two nodes with a socketpair. Returns the two link objects."""
    s1, s2 = socket.socketpair()
    f1 = (s1.makefile("rb"), s1.makefile("wb"))
    f2 = (s2.makefile("rb"), s2.makefile("wb"))
    l1 = a.make_link(f1[0], f1[1], kind, closer=lambda: _shut(s1))
    l2 = b.make_link(f2[0], f2[1], kind, closer=lambda: _shut(s2))
    return l1, l2


def _shut(sock):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except socket.error:
        pass
    sock.close()


def new_node(name, authority=None, fast=True):
    node = Node(name, authority=authority)
    if fast:
        node.ping_interval = 0.2
        node.announce_interval = 0.2
        node.alive_timeout = 2.0
    node.start()
    return node


def wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False
