# -*- coding: utf-8 -*-
"""A mesh node: links, packet dispatch and forwarding, routing, ping.

All state changes happen under self.lock. Link I/O runs in link threads and
only queues frames, so holding the lock never waits on a pipe.
"""

import threading
import time
import traceback

from agentipc import proto
from agentipc.link import Link
from agentipc.routing import Pings, SeenCache, Topology


class Node(object):
    def __init__(self, node_id, authority=None):
        self.id = node_id
        self.authority = authority
        self.lock = threading.RLock()
        self.links = []           # established links (hello done)
        self.attached = []        # every open link
        self.topology = Topology(node_id)
        self.pings = Pings()
        self.seen = SeenCache()
        self.handlers = {}        # packet kind -> fn(packet)
        self.requests = {}        # request id -> callback(payload)
        self.services = []        # objects with on_link_up(link) and on_node_gone(node)
        self.oseq = 0             # sequence for records originated here
        self.ping_interval = 10.0
        self.announce_interval = 30.0
        self.node_timeout = 120.0 # forget a node after this long without an announcement
        self.alive_timeout = 30.0 # a node is alive if it answered a ping this recently
        self.debug = False
        self._running = False
        self.register("ping", self._h_ping)
        self.register("pong", self._h_pong)
        self.register("announce", self._h_announce)

    # ----- setup -------------------------------------------------------

    def register(self, kind, fn):
        self.handlers[kind] = fn

    def add_service(self, service):
        self.services.append(service)

    def is_authority(self):
        return self.authority == self.id

    def make_link(self, rfile, wfile, kind, closer=None):
        """Wrap a byte stream as a link, say hello, and start it."""
        link = Link(rfile, wfile, kind, self._on_frame, self._on_close, closer)
        # Our hello must be queued before the threads start: the peer's hello
        # may be processed at once, and nothing may precede ours on the wire.
        link.send(proto.hello(self.id, kind))
        with self.lock:
            self.attached.append(link)
        link.start()
        return link

    def start(self):
        self._running = True
        thread = threading.Thread(target=self._tick_loop, name="node-tick")
        thread.daemon = True
        thread.start()

    def stop(self):
        self._running = False

    # ----- sending -----------------------------------------------------

    def send(self, dst, kind, payload):
        """Originate a packet. Returns False if no route exists."""
        pkt = proto.packet(self.id, dst, kind, payload)
        with self.lock:
            self.seen.add((self.id, pkt["id"]))
            if dst == self.id:
                self._dispatch(pkt)
                return True
            if dst == proto.FLOOD:
                self._dispatch(pkt)
                self._flood(pkt, None)
                return True
            return self._route(pkt)

    def request(self, dst, kind, payload, callback):
        """Send a packet whose reply is handed to callback(payload) by resolve()."""
        req = proto.new_id()
        body = dict(payload)
        body["req"] = req
        with self.lock:
            self.requests[req] = callback
            if not self.send(dst, kind, body):
                self.requests.pop(req, None)
                return False
        return True

    def resolve(self, payload):
        with self.lock:
            callback = self.requests.pop(payload.get("req"), None)
        if callback is not None:
            callback(payload)

    def next_oseq(self):
        with self.lock:
            self.oseq += 1
            return self.oseq

    # ----- receiving ---------------------------------------------------

    def _on_frame(self, link, frame):
        with self.lock:
            kind = frame["type"]
            if kind == "hello":
                self._on_hello(link, frame)
            elif kind == "bye":
                link.close()
            elif link.peer is None:
                link.close("bad-frame")
            else:
                self._on_packet(link, frame)

    def _on_hello(self, link, frame):
        if frame["version"] != proto.PROTOCOL_VERSION:
            link.close("version")
            return
        if frame["node"] == self.id:
            link.close("self-link")
            return
        if link.peer is not None:
            link.close("bad-frame")
            return
        link.peer = frame["node"]
        self.links.append(link)
        self._announce_now()
        for service in self.services:
            service.on_link_up(link)

    def _on_close(self, link):
        with self.lock:
            if link in self.attached:
                self.attached.remove(link)
            if link in self.links:
                self.links.remove(link)
                self._announce_now()

    def _on_packet(self, link, pkt):
        if not self.seen.add((pkt["src"], pkt["id"])):
            return
        dst = pkt["dst"]
        if dst in (self.id, proto.FLOOD):
            self._dispatch(pkt)
        if dst == self.id or pkt["ttl"] <= 1:
            return
        out = dict(pkt, ttl=pkt["ttl"] - 1)
        if dst == proto.FLOOD:
            self._flood(out, link)
        else:
            self._route(out)

    def _dispatch(self, pkt):
        handler = self.handlers.get(pkt["kind"])
        if handler is None:
            return
        try:
            handler(pkt)
        except Exception:
            traceback.print_exc()

    # ----- forwarding --------------------------------------------------

    def _route(self, pkt):
        link = self._link_to(self.topology.next_hop(pkt["dst"]))
        if link is None:
            return False
        link.send(pkt)
        return True

    def _flood(self, pkt, exclude):
        for link in list(self.links):
            if link is not exclude:
                link.send(pkt)

    def _link_to(self, peer):
        if peer is None:
            return None
        for link in self.links:
            if link.peer == peer and not link.closed:
                return link
        return None

    # ----- routing and ping --------------------------------------------

    def _neighbours(self):
        return sorted(set(link.peer for link in self.links))

    def _announce_now(self):
        neighbours = self._neighbours()
        seq = self.topology.set_own(neighbours)
        self.send(proto.FLOOD, "announce", {"seq": seq, "links": neighbours})

    def _h_announce(self, pkt):
        payload = pkt["payload"]
        seq = payload.get("seq")
        links = payload.get("links")
        if not isinstance(seq, int) or not isinstance(links, list):
            return
        for name in links:
            proto.check_node(name)
        self.topology.update(pkt["src"], seq, links, time.time())

    def _h_ping(self, pkt):
        self.send(pkt["src"], "pong", {"t": pkt["payload"].get("t")})

    def _h_pong(self, pkt):
        sent = pkt["payload"].get("t")
        if isinstance(sent, float) or isinstance(sent, int):
            now = time.time()
            self.pings.pong(pkt["src"], now - sent, now)

    def status(self):
        """Known nodes with liveness and round-trip time."""
        now = time.time()
        with self.lock:
            rows = []
            for name in self.topology.nodes():
                row = self.pings.status(name, now, self.alive_timeout)
                row["via"] = self.topology.next_hop(name)
                rows.append(row)
            return rows

    def _tick_loop(self):
        last_ping = last_announce = 0.0
        while self._running:
            now = time.time()
            with self.lock:
                if now - last_announce >= self.announce_interval:
                    self._announce_now()
                    last_announce = now
                if now - last_ping >= self.ping_interval:
                    for name in self.topology.nodes():
                        self.send(name, "ping", {"t": now})
                    last_ping = now
                for origin in self.topology.expire(now, self.node_timeout):
                    for service in self.services:
                        service.on_node_gone(origin)
            time.sleep(0.05)
