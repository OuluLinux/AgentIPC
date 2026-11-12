# -*- coding: utf-8 -*-
"""Read positions for `vs wait`, kept per session home.

A position is, for one user, server and channel, the highest oseq seen from each
origin node. Origins count their own messages up, so every message at or below
that number has been seen. A record is unseen when its oseq is above the number
for its origin.
"""

import json
import os
import tempfile

POSITIONS_FILE = "vs-positions.json"


def _path(home):
    return os.path.join(home, POSITIONS_FILE)


def load(home):
    try:
        with open(_path(home)) as fh:
            data = json.load(fh)
    except (IOError, OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(home, data):
    if not os.path.isdir(home):
        os.makedirs(home)
    fd, tmp = tempfile.mkstemp(dir=home, prefix=POSITIONS_FILE + ".")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.rename(tmp, _path(home))
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def key(user, server, chan):
    return "%s@%s#%s" % (user, server, chan.lstrip("#").lower())


def unseen(rec, have):
    """True if rec is above the position for its origin."""
    return rec["oseq"] > have.get(rec["origin"], 0)


def advance(have, rec):
    """Moves the position past rec. Call for every record taken in, matching or not."""
    if rec["oseq"] > have.get(rec["origin"], 0):
        have[rec["origin"]] = rec["oseq"]
