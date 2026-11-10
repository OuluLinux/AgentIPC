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
