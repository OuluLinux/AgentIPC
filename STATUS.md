# AgentIPC status

Last updated: 2026-11-10. Latest code commit: see `git log`. 76 tests.

This file lists what is implemented, what was tested and how, what is not
done, and known gaps. [DESIGN.md](DESIGN.md) describes the design.

## Priorities

1. Small C node (phase 11). Router in `cnode/` (see `cnode/README.md`): versioned handshake, routing, forwarding, duplicate suppression, pings, direct listener. Running on gen7. Still to do: virtual server, shell, file transfer.
2. Android app (phase 06). Sketch only; needs C-node-level work to be useful on the same mesh.
3. Link encryption (phase 10), then obfuscated HTTP/HTTPS (note only).

## Implemented and tested

Automated tests: 71 tests, all passing under Python 3, with and without PyNaCl (2 skip without PyNaCl).

| Area | What works | How it was tested |
|---|---|---|
| Local channels | join, leave, say, read (new, wait, tail, since), who, channels, JSON output | unit tests, CLI |
| Wire protocol v1 | framing, validation, `hello`, `bye`, routed `packet` | unit tests |
| Multicast packets | one `dst` list, one copy per shared link, dedupe per destination | unit and in-process mesh tests; not measured on live links |
| Routing | link-state announces, shortest-path next hops, TTL, ping and RTT, node-loss cleanup | in-process tests; live tests on three machines |
| Routing fixes | a route needs both ends to announce the link; new links get the current topology; restarts are seen at once | in-process convergence tests; live restart test |
| Links over ssh | dial over ssh, reconnect with backoff, bridge commands | end-to-end tests with a fake ssh; live ssh star |
| Direct TCP links | allowlisted addresses only, refused with `ERR not-allowed`, reconnect | end-to-end tests; live direct star and triangle |
| Authority and login | accounts (pbkdf2), challenge-response login, HMAC tokens, verify over the mesh with a cache | unit and end-to-end tests; live logins |
| Virtual server | channels, membership, history sync, node-loss cleanup | in-process and end-to-end tests; live messages across chains and triangle |
| Encrypted virtual servers | PyNaCl SecretBox, key wrapped under the password key, relays see ciphertext | venv tests; relay files checked for plaintext |
| File transfer | 32 KiB chunks, out-of-order assembly, SHA-256 check (plain servers only) | unit tests; live 300 KB chain and 200 KB triangle transfers, checksums matched |
| Login and token deadlines | login and token checks fail after 20 s with no answer | mesh request-timeout test; live login |
| Inbox per user | received files are filed under `user@node` | unit and end-to-end tests; live file transfer |
| Remote shell | opt-in per node, user allowlist, token checked by authority, timeout, output cap, exit code | unit and end-to-end tests; live exec on four nodes |
| Multiple instances per machine | `--home`, `node run --node/--control-port/--listen`, separate data | end-to-end test with two instances; live two instances on one machine |
| Remote start and rsync send | `remote start` and `send` build the right ssh and rsync commands | tests with a recording fake ssh and rsync; not run against a real sshd |
| Wait | `vs wait CHAN [--from USER]` blocks until a new message arrives from that user, prints it, exits | two end-to-end tests; two-session run on an encrypted server |
| Scenario | SKYLARK-MANAGER: two agent sessions on one computer, planned by S1 and built by S2 over files and short messages; setup and role messages in `docs/scenarios/skylark-manager.md` | run on an encrypted server with two accounts (not yet with real agent sessions) |
| Watch | `vs watch` prints live messages as they arrive | end-to-end test |
| Timers | ping, announce, node and alive timeouts set per node in config | config read at start; not separately tested |
| Protocol versions | wire v2 with version range and features; version 1 refused; client `VERSION` command | unit, in-process raw-peer and end-to-end tests; live mesh restarted together on v2 |
| Record numbers across restarts | a restarted node no longer reuses record numbers; posts after a restart are kept | unit test; live posts from every origin after a restart |
| Error text | error codes keep their code and add a plain reason | unit tests |
| Target bridge command | per-target `command` for `vs --ssh` | end-to-end tests; live |

Live test network (three machines, one of them the authority): ssh star, direct star,
three-hop chain, direct triangle, node loss and recovery, shell on all nodes.

## Not done

- Small C node (phase 11). Not started.
- Android app (phase 06). Kotlin sketch committed (`android/`), not compiled or run: no Android SDK on the development machine, and its java install is broken. Dependency versions are unchecked.
- Python 2.6 and 2.7 verification: see "Python versions" in DESIGN.md. 2.7 passes the full suite. 2.6 runs the nodes and the CLI, and passes the end-to-end suite with the tests driven by 2.7; its own unit tests cannot run (no `unittest discover`).
- Link encryption for direct links (phase 10). Allowed by the design (PyNaCl), not built.
- Obfuscated HTTP/HTTPS transport. Note only.
- Encrypted file transfer.
- Encrypted virtual servers on the live mesh. Tested only in the local test suite.
- Embedded library for host programs (phase 12). Idea only.
- Web UI (phase 08). Parked.
- Rekeying of encrypted servers on membership change.
- Pruning of stored records and membership. Storage grows without bounds.

## Known gaps

- **Manual authority failover.** The accounts are copied to a standby over ssh within 5 s of a change. If the authority is down, `account promote` on the standby and a restart take over. Changes made after the last copy are lost. No automatic failover (see DESIGN.md, "Authority failover").
- **Inbox folders.** Received files go in `user@node`, where `node` is where the upload entered the mesh. That is not always the machine the file came from.
- **Shell access is broad.** Any user on a node's `shell.users` list runs arbitrary commands as that node's user. Keep lists short.
- **Plain servers expose messages to relays.** Message bodies on a plain server are readable by every node that carries them.
- **Membership is not pruned.** Channel records can be pruned with `retention_days`; membership is current state and is kept.

## Test environment notes

- Development: Gentoo, Python 3.13 with a venv that has PyNaCl 1.6.2.
- Python 2 checks: iMac (macOS 10.11, Python 2.6.9 and 2.7.10, no Python 3, no PyNaCl). Its clone is at `~/OuluLinux/AgentIPC` and is synced from the development machine.
- Live machines: Gentoo and Linux x86_64, Python 3.14. One machine runs two instances.
- The authority's firewall allows the direct port only from the two LAN peers.
