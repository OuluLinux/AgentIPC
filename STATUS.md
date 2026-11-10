# AgentIPC status

Last updated: 2026-11-10. Commit `036a003` plus the status notes.

This file lists what is implemented, what was tested and how, what is not
done, and known gaps. [DESIGN.md](DESIGN.md) describes the design.

## Priorities

1. Small C node (phase 11). Plain links, remote shell, file transfer.
2. Android app (phase 06). Sketch only; needs C-node-level work to be useful on the same mesh.
3. Python 2.7 verification. **Postponed** until a Python 2.7 system is available (Gentoo py2.7 setup is hard).
4. Link encryption (phase 10), then obfuscated HTTP/HTTPS (note only).

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
| Remote shell | opt-in per node, user allowlist, token checked by authority, timeout, output cap, exit code | unit and end-to-end tests; live exec on four nodes |
| Multiple instances per machine | `--home`, `node run --node/--control-port/--listen`, separate data | end-to-end test with two instances; live two instances on one machine |
| Remote start and rsync send | `remote start` and `send` commands | written; **not run**, no test |
| Target bridge command | per-target `command` for `vs --ssh` | end-to-end tests; live |

Live test network (three machines, one of them the authority): ssh star, direct star,
three-hop chain, direct triangle, node loss and recovery, shell on all nodes.

## Not done

- Small C node (phase 11). Not started.
- Android app (phase 06). Kotlin sketch committed (`android/`), not compiled or run: no Android SDK on the development machine, and its java install is broken. Dependency versions are unchecked.
- Python 2.7 verification (phase 01 task 06 and the mesh code). Postponed. The code avoids Python 3-only syntax, but nothing has run on 2.7.
- Link encryption for direct links (phase 10). Allowed by the design (PyNaCl), not built.
- Obfuscated HTTP/HTTPS transport. Note only.
- Encrypted file transfer.
- Encrypted virtual servers on the live mesh. Tested only in the local test suite.
- Embedded library for host programs (phase 12). Idea only.
- Web UI (phase 08). Parked.
- Rekeying of encrypted servers on membership change.
- Pruning of stored records and membership. Storage grows without bounds.
- Protocol version bump to 2. Deliberately not done; the multicast change is compatible only with nodes running this commit or later.

## Known gaps

- **Single authority.** If the authority node is down, logins and token checks fail. No failover.
- **Sender in the inbox.** Received files are filed under the node where the transfer entered the mesh, not the sending user.
- **Shell access is broad.** Any user on a node's `shell.users` list runs arbitrary commands as that node's user. Keep lists short.
- **Plain servers expose messages to relays.** Message bodies on a plain server are readable by every node that carries them.
- **Control socket is local-only but unauthenticated.** Any local user can reach `127.0.0.1` control ports.
- **Refusal messages are terse.** Login errors are short codes, and so are direct-link refusals.
- **Request timeouts apply only to `exec`.** Other requests, such as login, have no deadline.
- **Python 2.7 untested.** See above.
- **Test processes.** The end-to-end tests can leave node processes behind if a run is interrupted.

## Test environment notes

- Development: Gentoo, Python 3.13 with a venv that has PyNaCl 1.6.2.
- Live machines: Gentoo and Linux x86_64, Python 3.14. One machine runs two instances.
- The authority's firewall allows the direct port only from the two LAN peers.
