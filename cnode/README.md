# agentipc C node

A node in plain C89 (POSIX sockets for the listener), for machines where Python
is not practical. It speaks the same wire protocol as `agentipc/` (see
`DESIGN.md`, "Versioning") and routes like `agentipc/node.py`.

## Build

    make -C cnode            # cc -std=c89 -pedantic -Wall -Wextra
    make -C cnode check      # JSON reader and writer tests

## Run

Over ssh (stdio), the link is the process's stdin and stdout. The node runs until
that link ends:

    cnode/cnode --stdio --node NAME [--announce-interval SECONDS]

As a direct listener on localhost, the node serves any number of links at once.
Each starts with `MODE link`, as Python's direct dialer sends it:

    cnode/cnode --listen PORT --node NAME [--announce-interval SECONDS]

The listener binds only 127.0.0.1. Any local user can connect and speak as this
node, so run it only where that is acceptable. Plain TCP is used on localhost,
since ssh is not involved there.

`--announce-interval` is how often the node re-announces itself (default 30 s).

## What it does

- **Handshake.** Sends `hello` (version, `min_version`, features `core`), checks the
  peer's, and refuses with a `bye` and the same reasons as the Python node. A new
  link from a peer replaces an older link to that peer.
- **Topology.** Stores each origin's announce (seq, links, ttl) and drops origins not
  heard from for 120 s. A route to a node exists only where both ends announce each
  other, as `routing.Topology` does. Direct peers are reached over their link.
- **Announces.** Announces itself when a link comes up and on the interval. Replays
  stored announces to a new link, one ttl lower.
- **Forwarding.** Unicast and multicast go one copy per outgoing link, carrying only
  the destinations routed through it. Floods are relayed to every other link.
- **Duplicates.** Each (src, id, destination) is handled once. The cache holds the
  last 512 keys.
- **Pings.** A ping addressed to the node is answered with a pong routed back to its
  source.
- **Refusals.** A malformed frame gets a `bye` with a reason, and that link is closed.
  In `--stdio` mode a refusal makes the process exit with status 2.

## Done

- JSON reader and writer (`json.c`). The writer escapes non-ASCII as `\uXXXX`, as
  Python's `json.dumps` does, so output stays ASCII. The reader decodes `\u` escapes,
  surrogate pairs included, to UTF-8.
- Handshake, topology, forwarding, duplicate suppression, announce replay, pings.
- Listener for direct links, serving several links in one event loop.
- Tests:
  - `tests/test_cnode.py` (34): handshake, refusals, the leaf behaviour, the listener,
    a live test (a real Python node links to the C binary through a fake ssh and sees
    it alive), and routing with fake Python peers (two hops, replies, duplicates,
    multicast, flood relay, TTL, no route, reconnect).
  - `make check`: JSON reader and writer.

## Not done yet

- Virtual server, shell and file transfer. The node does not answer those kinds;
  Python nodes refuse to send them to a node that announces only `core`.
- Write timeouts. A peer that stops reading can stall the loop, because writes block.
- Retro targets. Each one needs its own build and test run.
