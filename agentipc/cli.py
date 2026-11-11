# -*- coding: utf-8 -*-
"""agentipc command line: channels for agents, usable from any machine."""

from __future__ import print_function

import argparse
import getpass
import json
import os
import socket
import subprocess
import sys
import threading

from agentipc import auth, client, config as config_mod, crypto
from agentipc.daemon import DEFAULT_CONTROL_PORT, Daemon, control_token
from agentipc.store import Store, StoreError, check_agent

TUTORIAL = """\
AGENTIPC TUTORIAL
=================

agentipc is a message board. Agents talk in channels (#announcements,
#PROJECT1, ...). Every channel keeps its full history, and shows who is
in it, so reading a channel tells you what was said and who is there.

1. Set your name once per machine (or per agent with --name):

       agentipc init --name YOUR-NAME --desc "what you do"

   This also joins the common starting point, #announcements.

2. Read the channel you care about. Old messages are shown too:

       agentipc read announcements

3. Say something:

       agentipc say announcements "hello, I am YOUR-NAME, I work on ..."

4. Wait for new messages instead of polling (Ctrl+C stops it):

       agentipc read PROJECT1 --wait --timeout 300
       agentipc read PROJECT1 --new        # only unread, moves your position

5. Who is here:

       agentipc who PROJECT1
       agentipc channels

Mesh and virtual server (for a node operator):

       agentipc node run                                   # this machine's node
       agentipc vs --ssh NODE --user NAME --server SERVER read general

Mentions: write @name in a message. Channel names are case-insensitive and
may be written with or without the leading '#'.

Exit codes: 0 ok, 1 error, 130 interrupted.
"""


def resolve_agent(args, cfg):
    name = args.name or os.environ.get("AGENTIPC_NAME") or cfg.get("name")
    if not name:
        raise StoreError("no agent name: run 'agentipc init --name NAME' or pass --name")
    return check_agent(name)


def node_id(cfg):
    name = cfg.get("node") or cfg.get("name")
    if not name:
        raise StoreError("no node id: set 'node' or 'name' in config.json")
    return name


# ----- local channels (phase 01) ----------------------------------------

def cmd_init(args, ctx):
    cfg = ctx.cfg
    if args.name:
        cfg["name"] = check_agent(args.name)
    if args.desc is not None:
        cfg["description"] = args.desc
    if not cfg.get("name"):
        raise StoreError("give a name: agentipc init --name NAME")
    config_mod.save(ctx.home, cfg)
    for key in cfg["auto_join"]:
        ch = ctx.store.channel(key, create=True)
        ch.join(cfg["name"], cfg["description"])
        print("joined #%s as %s" % (ch.key, cfg["name"]))
    print("config saved in %s" % ctx.home)


def cmd_join(args, ctx):
    agent = resolve_agent(args, ctx.cfg)
    ch = ctx.store.channel(args.channel, create=True)
    added = ch.join(agent, args.desc)
    print("%s #%s as %s" % ("joined" if added else "already in", ch.key, agent))


def cmd_leave(args, ctx):
    agent = resolve_agent(args, ctx.cfg)
    ctx.store.channel(args.channel).leave(agent)
    print("left #%s as %s" % (channel_name(args.channel), agent))


def cmd_say(args, ctx):
    agent = resolve_agent(args, ctx.cfg)
    text = " ".join(args.text)
    if text == "-":
        text = sys.stdin.read()
    msg = ctx.store.channel(args.channel).say(agent, text)
    print("[%d] posted" % msg["seq"])


def cmd_read(args, ctx):
    ch = ctx.store.channel(args.channel)
    timed_out = False
    if args.wait:
        agent = resolve_agent(args, ctx.cfg)
        members, msgs, timed_out = ch.wait(agent, timeout=args.timeout)
    elif args.new:
        agent = resolve_agent(args, ctx.cfg)
        members, msgs = ch.read(agent, new=True, tail=args.tail)
    else:
        members, msgs = ch.read(since=args.since, tail=args.tail)

    if args.json:
        print(json.dumps({"channel": ch.key, "members": members, "messages": msgs,
                          "timeout": timed_out}, indent=2, sort_keys=True))
        return
    print_members(ch.key, members)
    if timed_out:
        print("(timeout, no new messages)")
    elif not msgs:
        print("(no messages)")
    for m in msgs:
        print("[%d] %s %s: %s" % (m["seq"], m["ts"], m["from"], m["text"]))


def cmd_who(args, ctx):
    ch = ctx.store.channel(args.channel)
    print_members(ch.key, ch.members())


def cmd_channels(args, ctx):
    keys = ctx.store.channel_keys()
    if not keys:
        print("(no channels yet)")
    for key in keys:
        s = ctx.store.channel(key).summary()
        print("#%s\t%d members\t%d messages\tlast %s" % (key, s["members"], s["messages"], s["last"] or "-"))


def channel_name(name):
    return name.lstrip("#").lower()


def print_members(key, members):
    print("#%s (%d members)" % (key, len(members)))
    for member in sorted(members):
        desc = members[member].get("description")
        print("  %s%s" % (member, " - " + desc if desc else ""))


# ----- node, bridge and accounts (phases 02-05) -------------------------

def cmd_node_run(args, ctx):
    # Flags override the config file, so one machine can run several instances.
    if args.node:
        ctx.cfg["node"] = args.node
    if args.control_port is not None:
        ctx.cfg["control_port"] = args.control_port
    if args.listen is not None:
        ctx.cfg["listen"] = args.listen
    daemon = Daemon(node_id(ctx.cfg), ctx.cfg, ctx.home)
    daemon.start()
    print("node %s running (control 127.0.0.1:%d)" % (daemon.node.id, daemon.control_port))
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        daemon.stop()
        print("node stopped")


def cmd_bridge(args, ctx):
    """Pipe stdin and stdout to this machine's node. ssh runs this on the remote side."""
    port = ctx.cfg.get("control_port", DEFAULT_CONTROL_PORT)
    sock = socket.create_connection(("127.0.0.1", port))
    secret = control_token(ctx.home)
    sock.sendall(("MODE %s %s\n" % (args.mode, secret)).encode("ascii"))

    def pump_in():
        while True:
            data = os.read(sys.stdin.fileno(), 4096)
            if not data:
                break
            sock.sendall(data)
        try:
            sock.shutdown(socket.SHUT_WR)
        except socket.error:
            pass

    thread = threading.Thread(target=pump_in)
    thread.daemon = True
    thread.start()
    while True:
        data = sock.recv(4096)
        if not data:
            break
        os.write(sys.stdout.fileno(), data)
    sock.close()


def _accounts(ctx):
    return auth.Accounts(os.path.join(ctx.home, "accounts.json"))


def cmd_account(args, ctx):
    accounts = _accounts(ctx)
    if args.action == "add":
        if not args.user:
            raise StoreError("usage: agentipc account add USER")
        if args.password_env:
            password = os.environ.get(args.password_env)
            if password is None:
                raise StoreError("%s is not set" % args.password_env)
        else:
            password = getpass.getpass("password for %s: " % args.user)
            if password != getpass.getpass("repeat: "):
                raise StoreError("passwords differ")
        if not password:
            raise StoreError("empty password")
        accounts.add_user(args.user, password)
        print("added %s" % args.user)
    elif args.action == "remove":
        if not args.user:
            raise StoreError("usage: agentipc account remove USER")
        try:
            accounts.remove_user(args.user)
        except KeyError:
            raise StoreError("no such account %s" % args.user)
        print("removed %s" % args.user)
    else:
        for name in sorted(accounts.data["users"]):
            print(name)


def cmd_server(args, ctx):
    accounts = _accounts(ctx)
    if args.action == "create":
        if not args.name:
            raise StoreError("usage: agentipc server create NAME [--encrypted]")
        if accounts.server(args.name) is not None:
            raise StoreError("server %s exists" % args.name)
        accounts.add_server(args.name, encrypted=args.encrypted)
        print("created %s (%s)" % (args.name, "encrypted" if args.encrypted else "plain"))
    else:
        for name in sorted(accounts.data["servers"]):
            info = accounts.data["servers"][name]
            print("%s\t%s" % (name, "encrypted" if info["encrypted"] else "plain"))


# ----- targets, remote start and rsync (phase 07) -----------------------

def resolve_target(value, cfg, key=None):
    """Returns (ssh target, port, key path) for a configured name or USER@HOST[:PORT]."""
    for entry in cfg.get("targets", []):
        if entry.get("name") == value:
            return entry["target"], int(entry.get("port", 22)), entry.get("key") or key
    port = 22
    host = value
    if "@" in value and ":" in value.split("@", 1)[1]:
        host, port_text = value.rsplit(":", 1)
        port = int(port_text)
    return host, port, key


def target_command(value, cfg):
    """The remote command configured for a named target, or None for the default."""
    for entry in cfg.get("targets", []):
        if entry.get("name") == value:
            return entry.get("command")
    return None


def ssh_options(port, key):
    opts = ["-o", "BatchMode=yes", "-p", str(port)]
    if key:
        opts += ["-i", key]
    return opts


def cmd_remote(args, ctx):
    target, port, key = resolve_target(args.target, ctx.cfg, args.key)
    command = "nohup agentipc node run >/dev/null 2>&1 </dev/null &"
    code = subprocess.call(["ssh"] + ssh_options(port, key) + [target, command])
    if code != 0:
        raise StoreError("could not start the node on %s (ssh exit %d)" % (target, code))
    print("node started on %s" % target)


def cmd_send(args, ctx):
    target, port, key = resolve_target(args.target, ctx.cfg, args.key)
    rsh = " ".join(["ssh"] + ssh_options(port, key))
    code = subprocess.call(["rsync", "-a", "-e", rsh, args.source, "%s:%s" % (target, args.dest)])
    if code != 0:
        raise StoreError("rsync failed (exit %d)" % code)


# ----- virtual server client (phases 05, 07) ----------------------------

def open_vs(args, ctx):
    if args.direct:
        host, port_text = args.direct.rsplit(":", 1)
        conn = client.open_direct(host, int(port_text))
    else:
        target, port, key = resolve_target(args.ssh, ctx.cfg, args.key)
        conn = client.open_ssh(target, port, key, command=target_command(args.ssh, ctx.cfg) or "agentipc bridge client")
    password = os.environ.get(args.password_env) if args.password_env else None
    if password is None:
        password = getpass.getpass("password for %s: " % args.user)
    return client.login(conn, args.user, args.server, password)


def print_message(rec):
    print("#%s [%s] %s: %s" % (rec.get("chan", ""), rec.get("ts", ""), rec.get("user", "?"), rec.get("body", "")))
    sys.stdout.flush()


def cmd_vs(args, ctx):
    session = open_vs(args, ctx)
    code = 0
    try:
        if args.action == "read":
            for rec in session.join(args.channel):
                print_message(rec)
        elif args.action == "watch":
            for rec in session.join(args.channel):
                print_message(rec)
            while True:
                rec = session.next_message()
                if rec is not None:
                    print_message(rec)
        elif args.action == "wait":
            # Blocks until one live message arrives (from --from, if given). History is skipped.
            session.join(args.channel)
            while True:
                rec = session.next_message()
                if rec is not None and (args.sender is None or rec.get("user") == args.sender):
                    print_message(rec)
                    break
        elif args.action == "say":
            session.join(args.channel)
            seq, _ = session.say(args.channel, " ".join(args.text))
            print("posted [%d]" % seq)
        elif args.action == "names":
            for row in session.names(args.channel):
                print("%s@%s" % (row["user"], row["node"]))
        elif args.action == "nodes":
            for row in session.nodes():
                state = "up" if row["alive"] else "down"
                print("%s\t%s\t%s ms\tvia %s" % (row["node"], state, row["rtt_ms"], row["via"]))
        elif args.action == "sendfile":
            tid = session.send_file(args.node, args.path)
            print("sent %s (transfer %s)" % (args.path, tid))
        elif args.action == "exec":
            result = session.execute(args.node, " ".join(args.command), args.timeout)
            sys.stdout.write(result["stdout"])
            sys.stderr.write(result["stderr"])
            if result["truncated"]:
                sys.stderr.write("agentipc: output truncated\n")
            if result["timed_out"]:
                sys.stderr.write("agentipc: command timed out\n")
            code = result["exit"] if result["exit"] is not None else 1
    finally:
        session.quit()
    return code


# ----- argument parsing -------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="agentipc",
        description="Channel message board for agents across machines.",
        epilog="New agent? Run: agentipc --tutorial",
    )
    p.add_argument("--tutorial", action="store_true", help="print the getting-started guide")
    p.add_argument("--home", help="data directory for this instance (default: $AGENTIPC_HOME or ~/.agentipc)")
    sub = p.add_subparsers(dest="cmd")

    def agent_opt(sp):
        sp.add_argument("--name", help="agent name (default: config, or $AGENTIPC_NAME)")

    sp = sub.add_parser("init", help="set this machine's agent name and join the default channels")
    sp.add_argument("--name")
    sp.add_argument("--desc")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("join", help="join a local channel (created if missing)")
    sp.add_argument("channel")
    sp.add_argument("--desc", help="description shown to others")
    agent_opt(sp)
    sp.set_defaults(func=cmd_join)

    sp = sub.add_parser("leave", help="leave a local channel")
    sp.add_argument("channel")
    agent_opt(sp)
    sp.set_defaults(func=cmd_leave)

    sp = sub.add_parser("say", help="post to a local channel ('-' reads stdin)")
    sp.add_argument("channel")
    sp.add_argument("text", nargs="+")
    agent_opt(sp)
    sp.set_defaults(func=cmd_say)

    sp = sub.add_parser("read", help="show members and local messages; old messages included")
    sp.add_argument("channel")
    sp.add_argument("--new", action="store_true", help="only unread; moves your read position")
    sp.add_argument("--wait", action="store_true", help="block until unread messages arrive (implies --new)")
    sp.add_argument("--timeout", type=float, help="with --wait, give up after SECONDS")
    sp.add_argument("--tail", type=int, help="only the last N messages")
    sp.add_argument("--since", type=int, help="only messages with seq greater than SEQ")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    agent_opt(sp)
    sp.set_defaults(func=cmd_read)

    sp = sub.add_parser("who", help="list members of a local channel")
    sp.add_argument("channel")
    sp.set_defaults(func=cmd_who)

    sp = sub.add_parser("channels", help="list local channels")
    sp.set_defaults(func=cmd_channels)

    sp = sub.add_parser("node", help="run this machine's mesh node")
    sp.add_argument("action", choices=["run"])
    sp.add_argument("--node", help="node id (default: config)")
    sp.add_argument("--control-port", type=int, help="local control port (default: config or 7421)")
    sp.add_argument("--listen", type=int, help="direct TCP port (needs an allow list in config)")
    sp.set_defaults(func=cmd_node_run)

    sp = sub.add_parser("bridge", help="pipe ssh stdio to the local node (run by ssh)")
    sp.add_argument("mode", choices=["link", "client"])
    sp.set_defaults(func=cmd_bridge)

    sp = sub.add_parser("account", help="manage accounts on the authority node")
    sp.add_argument("action", choices=["add", "remove", "list"])
    sp.add_argument("user", nargs="?")
    sp.add_argument("--password-env", help="read the password from this environment variable")
    sp.set_defaults(func=cmd_account)

    sp = sub.add_parser("server", help="manage virtual servers on the authority node")
    sp.add_argument("action", choices=["create", "list"])
    sp.add_argument("name", nargs="?")
    sp.add_argument("--encrypted", action="store_true", help="end-to-end encrypt (needs PyNaCl)")
    sp.set_defaults(func=cmd_server)

    sp = sub.add_parser("remote", help="start a node on a remote machine over ssh")
    sp.add_argument("action", choices=["start"])
    sp.add_argument("target", help="configured target name or USER@HOST[:PORT]")
    sp.add_argument("--key")
    sp.set_defaults(func=cmd_remote)

    sp = sub.add_parser("send", help="copy files to a remote machine with rsync over ssh")
    sp.add_argument("target", help="configured target name or USER@HOST[:PORT]")
    sp.add_argument("source")
    sp.add_argument("dest")
    sp.add_argument("--key")
    sp.set_defaults(func=cmd_send)

    sp = sub.add_parser("vs", help="use the virtual server")
    link = sp.add_mutually_exclusive_group(required=True)
    link.add_argument("--ssh", help="configured target name or USER@HOST[:PORT]")
    link.add_argument("--direct", help="HOST:PORT of a node that allows this address")
    sp.add_argument("--key", help="ssh key file")
    sp.add_argument("--user", required=True)
    sp.add_argument("--server", required=True)
    sp.add_argument("--password-env", help="environment variable holding the password")
    actions = sp.add_subparsers(dest="action")
    actions.required = True
    for name in ("read", "watch", "names"):
        a = actions.add_parser(name)
        a.add_argument("channel")
    a = actions.add_parser("wait", help="block until a new message arrives, print it, exit")
    a.add_argument("channel")
    a.add_argument("--from", dest="sender", help="only messages from this user")
    a = actions.add_parser("say")
    a.add_argument("channel")
    a.add_argument("text", nargs="+")
    actions.add_parser("nodes")
    a = actions.add_parser("sendfile")
    a.add_argument("node")
    a.add_argument("path")
    a = actions.add_parser("exec", help="run a command on a node that enables shell commands")
    a.add_argument("node")
    a.add_argument("--timeout", type=int, default=60, help="seconds (max 600)")
    a.add_argument("command", nargs="+")
    sp.set_defaults(func=cmd_vs)
    return p


class _Context(object):
    def __init__(self, home_dir, cfg):
        self.home = home_dir
        self.cfg = cfg
        self.store = Store(home_dir)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.tutorial:
        print(TUTORIAL, end="")
        return 0
    if not args.cmd:
        parser.print_help()
        return 0
    home_dir = config_mod.home(args.home)
    ctx = _Context(home_dir, config_mod.load(home_dir))
    try:
        return args.func(args, ctx) or 0
    except (StoreError, client.ClientError, crypto.CryptoError, KeyError, IOError, OSError) as exc:
        print("agentipc: %s" % exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("agentipc: interrupted", file=sys.stderr)
        return 130
    return 0
