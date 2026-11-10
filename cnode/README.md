# agentipc C node

A small node in plain C89, for machines where Python is not practical.
It speaks the same wire protocol as `agentipc/` (see `DESIGN.md`, "Versioning").

## Build

    make -C cnode            # cc -std=c89 -pedantic -Wall -Wextra
    make -C cnode check      # JSON reader tests

## Run

Over ssh (stdio), the link is the process's stdin and stdout:

    cnode/cnode --stdio --node NAME [--link ssh|direct] [--announce-interval SECONDS]

As a direct link on localhost, the node listens on 127.0.0.1 and serves one link
after another. Python's direct dialer (`kind: direct`, `host: 127.0.0.1`) connects
and sends `MODE link` first. Plain TCP is used: localhost needs no encryption, so
ssh is not involved.

    cnode/cnode --listen PORT --node NAME [--announce-interval SECONDS]

The listener binds only 127.0.0.1. Any local user can connect to it and speak as
this leaf, so run it only on a machine where that is acceptable.

The link runs over stdin and stdout, which is what `ssh host cnode --stdio`
gives. The node sends its `hello`, reads the peer's, and checks the version
ranges, the node id and the frame shape. A refused link gets a `bye` with the
reason and exit code 2. A good handshake logs `linked to PEER at version N`
on stderr.

After the handshake the node is a leaf: it has one link and does no routing.
- Announces itself when the link comes up, and again when a ping arrives and
  the announce interval (default 30 s, as in `node.py`) has passed.
- Answers `ping` packets addressed to it (alone or in a list) with `pong`.
- Ignores packets not addressed to it, and floods, since a leaf does not relay.
- A `bye` from the peer, or the peer closing, ends the node with exit 0.
  A malformed packet gets a `bye bad-frame` and exit 2.

## Done

- JSON reader for wire frames (`json.c`), with tests.
- Hello: version and `min_version` negotiation, `features`, self-link check,
  and bad-frame checks on every field. Same reasons as the Python node.
- Leaf: link stays open, announces, answers pings, packet checks.
- Direct link on localhost: `--listen PORT` serves one link after another.
- Tests in `tests/test_cnode.py`:
  - handshake cases, driven with frames built by the Python `proto` module;
  - leaf behaviour, including refusals;
  - a live test: a real Python node links to the C binary through a fake ssh,
    and sees it alive;
  - listener tests: a direct link on localhost (two links in a row), and a
    non-link connection being closed.

## Not done yet

- Packet forwarding and routing. The node is a leaf only, so it must have one
  neighbour, and Python routing will not send traffic through it.
- Duplicate suppression. A leaf sees each flood once from its neighbour, so it
  does not keep a list of seen packets yet.
- Feature checks. The node states `features: ["core"]`. Python does not yet
  check features before sending a kind (for example `exec` to a node without
  `shell`).
- Virtual server, shell, file transfer.
- Retro targets. Each one needs its own build and test run.
