# -*- coding: utf-8 -*-
"""Virtual server across an authority and two members: login, channels, encryption, node loss."""

import os
import shutil
import socket
import tempfile
import threading
import unittest

from mesh_util import connect, new_node, wait_for
from agentipc import auth, client, vserver

try:
    import nacl.secret  # noqa: F401
    HAVE_NACL = True
except ImportError:
    HAVE_NACL = False


def attach_client(node, vs):
    """Run a ClientSession on the node over a socketpair. Returns a client.Connection."""
    server_side, client_side = socket.socketpair()
    session = vserver.ClientSession(vs, server_side.makefile("rb"), server_side.makefile("wb"))
    thread = threading.Thread(target=session.run)
    thread.daemon = True
    thread.start()
    return client.Connection(client_side.makefile("rb"), client_side.makefile("wb"),
                             closer=lambda: client_side.close())


def build_mesh(home):
    """Authority AUTH linked to member A, which is linked to member C."""
    nodes = {}
    accounts = auth.Accounts(os.path.join(home, "auth", "accounts.json"))
    authority = new_node("AUTH", authority="AUTH")
    auth.install(authority, accounts)
    nodes["AUTH"] = authority
    vs = {"AUTH": vserver.VirtualServer(authority, os.path.join(home, "auth", "records.jsonl"))}
    for name in ("A", "C"):
        node = new_node(name, authority="AUTH")
        auth.install(node)
        nodes[name] = node
        vs[name] = vserver.VirtualServer(node, os.path.join(home, name, "records.jsonl"))
    accounts.add_user("alice", "alice-pw")
    accounts.add_user("bob", "bob-pw")
    accounts.add_user("carol", "carol-pw")
    accounts.add_server("plain", encrypted=False)
    if HAVE_NACL:
        accounts.add_server("secret", encrypted=True)
    connect(authority, nodes["A"])
    connect(nodes["A"], nodes["C"])
    if not wait_for(lambda: nodes["C"].topology.next_hop("AUTH") == "A"):
        raise RuntimeError("mesh did not converge")
    return nodes, vs, accounts


class VirtualServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp()
        cls.nodes, cls.vs, cls.accounts = build_mesh(cls.home)
        cls.a, cls.c = cls.nodes["A"], cls.nodes["C"]

    @classmethod
    def tearDownClass(cls):
        for node in cls.nodes.values():
            node.stop()
        shutil.rmtree(cls.home, ignore_errors=True)

    def test_wrong_password_is_refused(self):
        conn = attach_client(self.a, self.vs["A"])
        with self.assertRaises(client.ClientError) as ctx:
            client.login(conn, "alice", "plain", "wrong")
        self.assertIn("bad-login", str(ctx.exception))

    def test_unknown_server_is_refused(self):
        conn = attach_client(self.a, self.vs["A"])
        with self.assertRaises(client.ClientError):
            client.login(conn, "alice", "nowhere", "alice-pw")

    def test_join_say_and_live_delivery_across_nodes(self):
        alice = client.login(attach_client(self.a, self.vs["A"]), "alice", "plain", "alice-pw")
        self.assertEqual(alice.join("general"), [])
        bob = client.login(attach_client(self.c, self.vs["C"]), "bob", "plain", "bob-pw")
        bob.join("general")
        self.assertTrue(wait_for(lambda: ("plain", "general", "bob", "C") in self.vs["A"].members))
        alice.say("general", "hello from A")
        msg = bob.next_message()
        self.assertEqual(msg["body"], "hello from A")
        self.assertEqual(msg["user"], "alice")

    def test_late_joiner_gets_history(self):
        alice = client.login(attach_client(self.a, self.vs["A"]), "alice", "plain", "alice-pw")
        alice.join("history")
        alice.say("history", "first")
        alice.say("history", "second")
        self.assertTrue(wait_for(lambda: len(self.vs["C"].records.channel("plain", "history")) == 2))
        carol = client.login(attach_client(self.c, self.vs["C"]), "carol", "plain", "carol-pw")
        bodies = [m["body"] for m in carol.join("history")]
        self.assertEqual(bodies, ["first", "second"])

    def test_names_lists_members_with_their_nodes(self):
        alice = client.login(attach_client(self.a, self.vs["A"]), "alice", "plain", "alice-pw")
        alice.join("roster")
        names = alice.names("roster")
        self.assertIn({"user": "alice", "node": "A"}, names)

    def test_token_login_on_another_node(self):
        alice = client.login(attach_client(self.a, self.vs["A"]), "alice", "plain", "alice-pw")
        token = alice.token
        again = client.login_token(attach_client(self.c, self.vs["C"]), token)
        self.assertEqual(again.server, "plain")

    def test_forged_token_is_refused(self):
        alice = client.login(attach_client(self.a, self.vs["A"]), "alice", "plain", "alice-pw")
        forged = alice.token[:-1] + ("0" if alice.token[-1] != "0" else "1")
        with self.assertRaises(client.ClientError):
            client.login_token(attach_client(self.c, self.vs["C"]), forged)

    def test_nodes_lists_the_mesh(self):
        alice = client.login(attach_client(self.a, self.vs["A"]), "alice", "plain", "alice-pw")
        self.assertTrue(wait_for(lambda: any(r["node"] == "AUTH" for r in alice.nodes())))

    @unittest.skipUnless(HAVE_NACL, "PyNaCl not installed")
    def test_encrypted_server_relays_only_ciphertext(self):
        alice = client.login(attach_client(self.a, self.vs["A"]), "alice", "secret", "alice-pw")
        self.assertTrue(alice.encrypted)
        alice.join("vault")
        bob = client.login(attach_client(self.c, self.vs["C"]), "bob", "secret", "bob-pw")
        self.assertTrue(bob.encrypted)
        bob.join("vault")
        self.assertTrue(wait_for(lambda: ("secret", "vault", "bob", "C") in self.vs["A"].members))
        alice.say("vault", "top secret plans")
        msg = bob.next_message()
        self.assertEqual(msg["body"], "top secret plans")
        for name in ("A", "C", "AUTH"):
            path = os.path.join(self.home, name if name != "AUTH" else "auth", "records.jsonl")
            with open(path) as fh:
                self.assertNotIn("top secret plans", fh.read())


class NodeLossTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.nodes, self.vs, _ = build_mesh(self.home)

    def tearDown(self):
        for node in self.nodes.values():
            node.stop()
        shutil.rmtree(self.home, ignore_errors=True)

    def test_dropped_node_takes_its_users_out_of_channels(self):
        bob_c = client.login(attach_client(self.nodes["C"], self.vs["C"]), "bob", "plain", "bob-pw")
        bob_c.join("drop")
        self.assertTrue(wait_for(lambda: ("plain", "drop", "bob", "C") in self.vs["A"].members))
        self.nodes["A"].node_timeout = 0.5
        for link in list(self.nodes["C"].attached):
            link.close()
        for link in list(self.nodes["A"].attached):
            if link.peer == "C":
                link.close()
        self.assertTrue(wait_for(lambda: not any(m[3] == "C" for m in self.vs["A"].members), 8))
