# -*- coding: utf-8 -*-
"""Routing, ping and flooding across in-process nodes."""

import time
import unittest

from mesh_util import connect, new_node, wait_for


class RoutingTest(unittest.TestCase):
    def setUp(self):
        self.nodes = []

    def tearDown(self):
        for node in self.nodes:
            node.stop()

    def node(self, name):
        node = new_node(name)
        self.nodes.append(node)
        return node

    def test_packet_crosses_middle_node(self):
        a, b, c = self.node("A"), self.node("B"), self.node("C")
        got = []
        c.register("test", lambda p: got.append(p["payload"]))
        connect(a, b)
        connect(b, c)
        self.assertTrue(wait_for(lambda: a.topology.next_hop("C") == "B"))
        self.assertTrue(a.send("C", "test", {"n": 1}))
        self.assertTrue(wait_for(lambda: got))
        self.assertEqual(got, [{"n": 1}])

    def test_no_route_is_reported(self):
        a = self.node("A")
        self.assertFalse(a.send("nobody", "test", {}))

    def test_flood_reaches_each_node_once_in_a_triangle(self):
        a, b, c = self.node("A"), self.node("B"), self.node("C")
        counts = {"B": 0, "C": 0}
        b.register("flood", lambda p: counts.__setitem__("B", counts["B"] + 1))
        c.register("flood", lambda p: counts.__setitem__("C", counts["C"] + 1))
        connect(a, b)
        connect(b, c)
        connect(c, a)
        self.assertTrue(wait_for(lambda: a.topology.next_hop("C") == "C"))
        a.send("*", "flood", {})
        self.assertTrue(wait_for(lambda: counts["B"] == 1 and counts["C"] == 1))
        time.sleep(0.3)
        self.assertEqual(counts, {"B": 1, "C": 1})

    def test_route_moves_around_a_failed_link(self):
        a, b, c = self.node("A"), self.node("B"), self.node("C")
        connect(a, b)
        connect(b, c)
        ac, ca = connect(a, c)
        self.assertTrue(wait_for(lambda: a.topology.next_hop("C") == "C"))
        ac.close()
        ca.close()
        self.assertTrue(wait_for(lambda: a.topology.next_hop("C") == "B"))

    def test_ping_reports_alive_and_rtt(self):
        a, b = self.node("A"), self.node("B")
        connect(a, b)
        self.assertTrue(wait_for(lambda: "B" in a.pings.last))
        rows = a.status()
        self.assertEqual(rows[0]["node"], "B")
        self.assertTrue(rows[0]["alive"])
        self.assertIsNotNone(rows[0]["rtt_ms"])

    def test_unreachable_node_is_marked_not_alive(self):
        a, b = self.node("A"), self.node("B")
        link_ab, link_ba = connect(a, b)
        self.assertTrue(wait_for(lambda: "B" in a.pings.last))
        link_ab.close()
        link_ba.close()
        a.alive_timeout = 0.3
        self.assertTrue(wait_for(lambda: all(not r["alive"] for r in a.status())
                                 and a.status(), 5))


if __name__ == "__main__":
    unittest.main()


class ConvergenceTest(unittest.TestCase):
    def test_new_link_learns_the_whole_topology_without_waiting_for_an_announce(self):
        # Default timers: periodic announces every 30 s, so anything learned here comes
        # from the link-up replay. A node that linked earlier must pass its neighbours on.
        a, b, c = new_node("A", fast=False), new_node("B", fast=False), new_node("C", fast=False)
        try:
            connect(c, a)
            self.assertTrue(wait_for(lambda: c.topology.next_hop("A") == "A"))
            connect(a, b)
            self.assertTrue(wait_for(lambda: b.topology.next_hop("C") == "A", timeout=3))
            self.assertTrue(wait_for(lambda: c.topology.next_hop("B") == "A", timeout=3))
        finally:
            for node in (a, b, c):
                node.stop()


class MulticastTest(unittest.TestCase):
    """One packet per shared link, delivered once to each destination."""

    def setUp(self):
        self.nodes = []

    def tearDown(self):
        for node in self.nodes:
            node.stop()

    def node(self, name):
        node = new_node(name)
        self.nodes.append(node)
        return node

    def count_frames(self, link, kind):
        """Wrap a link's send so every packet of this kind leaving on it is counted."""
        sent = []
        original = link.send

        def send(frame):
            if frame.get("type") == "packet" and frame["kind"] == kind:
                sent.append(frame)
            original(frame)

        link.send = send
        return sent

    def test_shared_link_carries_one_packet_for_two_destinations(self):
        a, b, c, d = self.node("A"), self.node("B"), self.node("C"), self.node("D")
        got = {"C": [], "D": []}
        c.register("multi", lambda p: got["C"].append(p["payload"]))
        d.register("multi", lambda p: got["D"].append(p["payload"]))
        ab, _ = connect(a, b)
        connect(b, c)
        connect(b, d)
        self.assertTrue(wait_for(lambda: a.topology.next_hop("C") == "B" and a.topology.next_hop("D") == "B"))
        frames = self.count_frames(ab, "multi")
        self.assertTrue(a.send(["C", "D"], "multi", {"n": 1}))
        self.assertTrue(wait_for(lambda: got["C"] and got["D"]))
        time.sleep(0.2)
        self.assertEqual(len(frames), 1)
        self.assertEqual(sorted(frames[0]["dst"]), ["C", "D"])
        self.assertEqual(got, {"C": [{"n": 1}], "D": [{"n": 1}]})

    def test_destination_reached_by_two_paths_is_delivered_once(self):
        a, b, c, d = self.node("A"), self.node("B"), self.node("C"), self.node("D")
        got = []
        d.register("multi", lambda p: got.append(p["payload"]))
        b.register("multi", lambda p: got.append(("B", p["payload"])))
        connect(a, b)
        connect(a, c)
        connect(b, d)
        connect(c, d)
        self.assertTrue(wait_for(lambda: a.topology.next_hop("D") is not None))
        self.assertTrue(a.send(["B", "D"], "multi", {"n": 2}))
        self.assertTrue(wait_for(lambda: len(got) >= 2))
        time.sleep(0.3)
        self.assertEqual(sorted(got, key=repr), sorted([{"n": 2}, ("B", {"n": 2})], key=repr))

    def test_ping_round_reaches_every_node(self):
        a, b, c = self.node("A"), self.node("B"), self.node("C")
        connect(a, b)
        connect(b, c)
        self.assertTrue(wait_for(lambda: "B" in a.pings.last and "C" in a.pings.last))


class RequestTimeoutTest(unittest.TestCase):
    def test_unanswered_request_fails_after_its_deadline(self):
        a, b = new_node("A"), new_node("B")
        try:
            connect(a, b)
            self.assertTrue(wait_for(lambda: a.topology.next_hop("B") == "B"))
            replies = []
            # B has no handler for "silent", so nothing ever answers.
            self.assertTrue(a.request("B", "silent", {}, replies.append, timeout=0.3))
            self.assertTrue(wait_for(lambda: replies))
            self.assertEqual(replies[0]["error"], "timeout")
        finally:
            a.stop()
            b.stop()
