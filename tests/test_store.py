# -*- coding: utf-8 -*-
"""Run with: python -m unittest discover -s tests   (Python 2.7 and 3)"""

import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from agentipc.store import Store, StoreError, channel_key  # noqa: E402


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.store = Store(self.root)

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_channel_names_are_case_and_hash_insensitive(self):
        self.assertEqual(channel_key("#PROJECT1"), "project1")
        self.assertEqual(channel_key("project1"), "project1")

    def test_invalid_names_rejected(self):
        for bad in ["", "#", "a/b", "x" * 65, "sp ace"]:
            self.assertRaises(StoreError, channel_key, bad)

    def test_join_say_read_new_and_history(self):
        ch = self.store.channel("announcements", create=True)
        ch.say("planner", "old message")
        self.assertTrue(ch.join("worker", "does tasks"))
        ch.say("planner", "task 3 ready for @worker")

        members, msgs = ch.read("worker", new=True)
        self.assertEqual(sorted(members), ["worker"])
        self.assertEqual([m["text"] for m in msgs],
                         ["old message", "worker joined", "task 3 ready for @worker"])
        self.assertEqual(msgs[-1]["mentions"], ["worker"])

        _, msgs = ch.read("worker", new=True)
        self.assertEqual(msgs, [])

        ch.say("planner", "next")
        _, msgs = ch.read("worker", new=True)
        self.assertEqual([m["text"] for m in msgs], ["next"])

    def test_join_again_updates_description_only(self):
        ch = self.store.channel("p", create=True)
        self.assertTrue(ch.join("a", "first"))
        self.assertFalse(ch.join("a", "second"))
        self.assertEqual(ch.members()["a"]["description"], "second")
        self.assertEqual(ch.summary()["messages"], 1)

    def test_leave_posts_and_removes(self):
        ch = self.store.channel("p", create=True)
        ch.join("a")
        ch.leave("a")
        self.assertEqual(ch.members(), {})
        _, msgs = ch.read()
        self.assertEqual(msgs[-1]["text"], "a left")
        self.assertRaises(StoreError, ch.leave, "a")

    def test_new_requires_membership(self):
        ch = self.store.channel("p", create=True)
        self.assertRaises(StoreError, ch.read, "ghost", True)

    def test_read_channel_must_exist(self):
        self.assertRaises(StoreError, self.store.channel, "missing")

    def test_wait_times_out(self):
        ch = self.store.channel("p", create=True)
        ch.join("a")
        ch.read("a", new=True)
        _, msgs, timed_out = ch.wait("a", timeout=0, interval=0.01)
        self.assertEqual(msgs, [])
        self.assertTrue(timed_out)

    def test_wait_returns_when_message_arrives(self):
        ch = self.store.channel("p", create=True)
        ch.join("a")
        ch.read("a", new=True)
        t = threading.Timer(0.1, ch.say, args=("b", "hi"))
        t.start()
        _, msgs, timed_out = ch.wait("a", timeout=5, interval=0.01)
        t.join()
        self.assertFalse(timed_out)
        self.assertEqual(msgs[-1]["text"], "hi")

    def test_concurrent_says_get_unique_contiguous_seq(self):
        ch = self.store.channel("p", create=True)
        threads = [threading.Thread(target=ch.say, args=("w%d" % i, "m%d" % i)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        _, msgs = ch.read()
        self.assertEqual([m["seq"] for m in msgs], list(range(1, 21)))


if __name__ == "__main__":
    unittest.main()
