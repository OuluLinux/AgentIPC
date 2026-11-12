# Notes for agents working on AgentIPC

## Joining the mesh

If you were asked to join AgentIPC, or to wait for instructions on a channel,
read [docs/tutorial.md](docs/tutorial.md) first. Sections 1 to 5 are what you
need. Section 8 is for starting a C node on a remote computer.

## Rules

- Do not paste passwords into channels, files, or commits. Use the environment
  variable the operator names.
- Do not restart, reconfigure, or change the accounts of a live node, and do not
  enable the remote shell, without the operator's go-ahead. Changes to a node's
  authority are made only as a planned failover (DESIGN.md, "Authority failover").
- Send paths and short sentences in messages. Put bulk content in files.
- Wait for instructions with `vs wait`, not with a polling loop.

## Working on the code

- Tests: `python3 -m unittest discover -s tests` (see README.md, "Tests").
- The C node builds with `make -C cnode`. Its tests run with `make -C cnode check`
  and the C-node tests in `tests/test_cnode.py`.
- Design and status: DESIGN.md and STATUS.md. Test output for Python 2.6 and 2.7
  is described in DESIGN.md, "Python versions".
