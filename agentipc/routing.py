# -*- coding: utf-8 -*-
"""Routing state: link-state topology, next hops, ping results, dedupe cache."""

import time
from collections import OrderedDict
from collections import deque


class Topology(object):
    """What every node announces about its neighbours, and the routes that follow."""

    def __init__(self, me):
        self.me = me
        # Start from the clock, so a restarted node's announcements outrank the ones
        # its neighbours still remember. Otherwise they are ignored until the old entry expires.
        self.seq = int(time.time() * 1000)
        self.graph = {me: set()}     # origin -> neighbours it announced
        self.stamps = {}             # origin -> (seq, time received)
        self.routes = {}             # destination -> first hop neighbour

    def set_own(self, neighbours):
        """Record this node's own neighbours. Returns the new announcement seq."""
        self.seq += 1
        self.graph[self.me] = set(neighbours)
        self._recompute()
        return self.seq

    def update(self, origin, seq, neighbours, now):
        """Apply a remote announcement. Returns True if it was newer."""
        if origin == self.me:
            return False
        old = self.stamps.get(origin)
        if old is not None and old[0] >= seq:
            return False
        self.stamps[origin] = (seq, now)
        self.graph[origin] = set(neighbours)
        self._recompute()
        return True

    def expire(self, now, max_age):
        """Forget origins not heard from within max_age. Returns the forgotten ids."""
        gone = [o for o, (_, t) in self.stamps.items() if now - t > max_age]
        for origin in gone:
            del self.stamps[origin]
            self.graph.pop(origin, None)
        if gone:
            self._recompute()
        return gone

    def next_hop(self, dst):
        return self.routes.get(dst)

    def nodes(self):
        """Every other node known to the topology."""
        return sorted((set(self.graph) | set(self.routes)) - set([self.me]))

    def _linked(self, a, b):
        """An edge counts only when both ends announce it. A one-sided claim is usually
        a stale announcement from a node whose link has gone."""
        return b in self.graph.get(a, ()) and a in self.graph.get(b, ())

    def _recompute(self):
        routes = {}
        queue = deque()
        for peer in sorted(self.graph.get(self.me, ())):
            if self._linked(self.me, peer):
                routes[peer] = peer
                queue.append(peer)
        while queue:
            node = queue.popleft()
            for nxt in sorted(self.graph.get(node, ())):
                if nxt != self.me and nxt not in routes and self._linked(node, nxt):
                    routes[nxt] = routes[node]
                    queue.append(nxt)
        self.routes = routes


class Pings(object):
    """Last pong seen from each node, and its round-trip time."""

    def __init__(self):
        self.rtt = {}
        self.last = {}

    def pong(self, node, rtt, now):
        self.rtt[node] = rtt
        self.last[node] = now

    def status(self, node, now, max_age):
        seen = self.last.get(node)
        alive = seen is not None and now - seen <= max_age
        return {"node": node, "alive": alive,
                "rtt_ms": int(self.rtt[node] * 1000) if node in self.rtt else None}


class SeenCache(object):
    """Recently seen (src, id) pairs, oldest dropped first."""

    def __init__(self, size=10000):
        self.size = size
        self._items = OrderedDict()

    def add(self, key):
        """Returns True if the key was new."""
        if key in self._items:
            return False
        self._items[key] = True
        if len(self._items) > self.size:
            self._items.popitem(last=False)
        return True
