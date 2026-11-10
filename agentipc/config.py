# -*- coding: utf-8 -*-
"""Per-machine settings in AGENTIPC_HOME/config.json (default ~/.agentipc)."""

import json
import os

DEFAULT_AUTO_JOIN = ["announcements"]


def home():
    return os.environ.get("AGENTIPC_HOME") or os.path.join(os.path.expanduser("~"), ".agentipc")


def load(home_dir):
    cfg = {"name": "", "description": "", "auto_join": list(DEFAULT_AUTO_JOIN)}
    p = os.path.join(home_dir, "config.json")
    if os.path.exists(p):
        with open(p) as fh:
            cfg.update(json.load(fh))
    return cfg


def save(home_dir, cfg):
    if not os.path.isdir(home_dir):
        os.makedirs(home_dir)
    with open(os.path.join(home_dir, "config.json"), "w") as fh:
        fh.write(json.dumps(cfg, indent=2, sort_keys=True) + "\n")
