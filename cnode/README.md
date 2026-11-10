# agentipc C node

A small node in plain C89, for machines where Python is not practical.
It speaks the same wire protocol as `agentipc/` (see `DESIGN.md`, "Versioning").

## Build

    make -C cnode            # cc -std=c89 -pedantic -Wall -Wextra
    make -C cnode check      # JSON reader tests

## Run (so far)

    cnode/cnode --stdio --node NAME [--link ssh|direct]

The link runs over stdin and stdout, which is what `ssh host cnode --stdio`
gives. The node sends its `hello`, reads the peer's, and checks the version
ranges, the node id and the frame shape. A refused link gets a `bye` with the
reason and exit code 2. A good handshake exits 0 with `linked to PEER at
version N` on stderr.

## Done

- JSON reader for wire frames (`json.c`), with tests.
- Hello frame: version and `min_version` negotiation, `features`, self-link check,
  bad-frame checks on every field. Same reasons as the Python node.
- Tests in `tests/test_cnode.py` drive the binary with frames built by the
  Python `proto` module, and check that the C node's hello decodes in Python.

## Not done yet

- Keeping the link open after the handshake. The node exits once the hello
  is checked.
- Packets, forwarding, announces and routing.
- Virtual server, shell, file transfer.
- Direct TCP listening.
- Testing against a live Python node over ssh.
- Retro targets (Python 2.7 is not involved here; the C targets are separate).
