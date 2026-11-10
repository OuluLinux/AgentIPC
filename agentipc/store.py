# -*- coding: utf-8 -*-
"""File-backed channel store. Python 2.7 and 3, stdlib only."""

import json
import os
import re
import time
import uuid

try:
    import fcntl
except ImportError:  # Windows, including Python 2
    fcntl = None
    import msvcrt

NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MENTION_RE = re.compile(r"@([A-Za-z0-9._-]{1,64})")
SYSTEM = "*"


class StoreError(Exception):
    pass


def utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def check_agent(name):
    if not NAME_RE.match(name or ""):
        raise StoreError("invalid agent name %r (letters, digits, . _ - ; max 64)" % name)
    return name


def channel_key(name):
    """'#Project1' and 'project1' name the same channel."""
    key = (name or "").lstrip("#").lower()
    if not NAME_RE.match(key):
        raise StoreError("invalid channel name %r (letters, digits, . _ - ; max 64)" % name)
    return key


def _write_atomic(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(text)
    if os.name == "nt" and os.path.exists(path):
        os.remove(path)  # os.rename cannot overwrite on Windows
    os.rename(tmp, path)


def _makedirs(path):
    if not os.path.isdir(path):
        try:
            os.makedirs(path)
        except OSError:
            if not os.path.isdir(path):
                raise


class _Lock(object):
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.fh = open(self.path, "a")
        self.fh.seek(0)
        if fcntl:
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
        else:
            msvcrt.locking(self.fh.fileno(), msvcrt.LK_LOCK, 1)
        return self

    def __exit__(self, *exc):
        self.fh.seek(0)
        if fcntl:
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        else:
            msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
        self.fh.close()
        return False


class Store(object):
    """All channels below one root directory."""

    def __init__(self, root):
        self.root = root

    def channel(self, name, create=False):
        key = channel_key(name)
        path = os.path.join(self.root, "channels", key)
        if not os.path.isdir(path):
            if not create:
                raise StoreError("no such channel #%s" % key)
            _makedirs(path)
        return Channel(key, path)

    def channel_keys(self):
        base = os.path.join(self.root, "channels")
        return sorted(os.listdir(base)) if os.path.isdir(base) else []


class Channel(object):
    def __init__(self, key, path):
        self.key = key
        self.path = path

    def _file(self, name):
        return os.path.join(self.path, name)

    def _lock(self):
        return _Lock(self._file("lock"))

    def _members(self):
        p = self._file("members.json")
        if not os.path.exists(p):
            return {}
        with open(p) as fh:
            return json.load(fh)

    def _save_members(self, members):
        _write_atomic(self._file("members.json"), json.dumps(members, indent=2, sort_keys=True) + "\n")

    def _messages(self):
        p = self._file("messages.jsonl")
        if not os.path.exists(p):
            return []
        with open(p) as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def _post(self, sender, text):
        """Caller must hold the lock."""
        msgs = self._messages()
        msg = {
            "seq": msgs[-1]["seq"] + 1 if msgs else 1,
            "id": uuid.uuid4().hex,
            "ts": utcnow(),
            "from": sender,
            "text": text,
            "mentions": MENTION_RE.findall(text),
        }
        with open(self._file("messages.jsonl"), "a") as fh:
            fh.write(json.dumps(msg, sort_keys=True) + "\n")
        return msg

    def members(self):
        with self._lock():
            return self._members()

    def join(self, agent, desc=None):
        """Returns True if the agent was newly added."""
        check_agent(agent)
        with self._lock():
            members = self._members()
            info = members.get(agent)
            if info is None:
                members[agent] = {"description": desc or "", "joined": utcnow(), "last_seen": 0}
                self._save_members(members)
                self._post(SYSTEM, "%s joined" % agent)
                return True
            if desc is not None and info["description"] != desc:
                info["description"] = desc
                self._save_members(members)
            return False

    def leave(self, agent):
        with self._lock():
            members = self._members()
            if agent not in members:
                raise StoreError("%s is not in #%s" % (agent, self.key))
            del members[agent]
            self._save_members(members)
            self._post(SYSTEM, "%s left" % agent)

    def say(self, agent, text):
        check_agent(agent)
        text = (text or "").strip()
        if not text:
            raise StoreError("empty message")
        with self._lock():
            return self._post(agent, text)

    def read(self, agent=None, new=False, since=None, tail=None):
        """Returns (members, messages). With new=True, only messages after the
        agent's last read; that position then moves to the last message shown."""
        with self._lock():
            members = self._members()
            msgs = self._messages()
            if new:
                if agent not in members:
                    raise StoreError("%s has not joined #%s" % (agent, self.key))
                since = members[agent]["last_seen"]
            selected = [m for m in msgs if m["seq"] > (since or 0)]
            if tail:
                selected = selected[-tail:]
            if new:
                members[agent]["last_seen"] = selected[-1]["seq"] if selected else since
                self._save_members(members)
        return members, selected

    def wait(self, agent, timeout=None, interval=1.0):
        """Block until there are unread messages. Returns (members, messages, timed_out)."""
        deadline = None if timeout is None else time.time() + timeout
        while True:
            members, msgs = self.read(agent, new=True)
            if msgs:
                return members, msgs, False
            if deadline is not None and time.time() >= deadline:
                return members, msgs, True
            time.sleep(interval)

    def summary(self):
        with self._lock():
            msgs = self._messages()
            return {
                "members": len(self._members()),
                "messages": len(msgs),
                "last": msgs[-1]["ts"] if msgs else None,
            }
