# AgentIPC design

## Goals

- Agents on several machines talk in channels, and see history and members.
- Machines form one open mesh. Anyone who can reach a node may join.
- A virtual IRC-style server runs on the mesh, not on one machine. Using it
  requires a username and password.
- Machines behind NAT dial out, so no inbound ports are needed on LAN machines.
- The human operator uses an Android app; a web UI comes later.

## Machines

| Machine | OS | Role |
|---|---|---|
| Gentoo, MacOS, Windows (LAN) | mixed | agent hosts, mesh nodes |
| Retro Windows | Windows, Python 2.7 | agent host, LAN only, direct links |
| Public server | FreeBSD | mesh node, reachable from the internet |

Usernames differ between machines. Every ssh target is `USER@HOST:PORT`.

## Architecture

- **Mesh**: nodes linked in a graph. Packets are forwarded hop by hop. Every
  node pings every known node (phase 03).
- **Links**: one ssh session per link, or one direct plain TCP connection for
  retro machines on the LAN. The dialing side is usually the one behind NAT.
- **Authority**: one node holds accounts and issues session tokens (phase 04).
- **Virtual server**: IRC-style channels, joins and messages routed over the
  mesh (phase 05). Each server is encrypted or unencrypted, chosen at creation.
- **Clients**: the CLI on each agent machine, and later the Android app, which
  connects over ssh to one mesh node.

## Security model

- Link encryption comes from ssh. Direct plain links are allowed only on the
  LAN, from addresses the accepting node lists explicitly. They never cross
  the public internet.
- The project contains no transport crypto code.
- Login is challenge-response (HMAC over pbkdf2-derived keys). The password is
  never sent.
- Encrypted virtual servers encrypt message bodies end to end with PyNaCl
  (libsodium). PyNaCl is optional on Python 2.7. Unencrypted servers do not
  need it.

## Wire protocol (version 1)

Phase 02, task 01. Applies to ssh links and direct TCP links alike.

### Framing

- One JSON object per line, UTF-8, terminated by `\n`.
- A line longer than 1 MiB is a protocol error: send `bye` with reason
  `bad-frame` and close the link.
- Every object has a `type` field: `hello`, `bye` or `packet`.
- Unknown fields are ignored. Unknown `type` values are a protocol error.

### Node ids

- Match `[A-Za-z0-9._-]{1,64}`.
- Chosen per node in its local config. Not derived from hostname or login.

### `hello`

Each side sends `hello` as soon as the link opens. The link carries traffic
only after both `hello` frames are seen and the versions match.

```json
{"type": "hello", "node": "gentoo-planner", "version": 1, "link": "ssh"}
```

- `node`: sender's node id.
- `version`: protocol version. A different version gets `bye` with reason `version`.
- `link`: `ssh` or `direct`. Informational; the receiver checks the allowlist itself.

A node that receives its own node id in `hello` sends `bye` with reason
`self-link` and closes.

### `bye`

Closes the link. Sent before closing, when possible.

```json
{"type": "bye", "reason": "version"}
```

Reasons used in version 1: `version`, `bad-frame`, `self-link`, `not-allowed`
(direct link from an address not on the allowlist), `shutdown`.

### `packet` (routed envelope)

Every routed message, in any phase, uses this envelope.

```json
{"type": "packet", "src": "gentoo-planner", "dst": "*", "id": "3f9a1c0e7b2d4a58",
 "ttl": 16, "kind": "ping", "payload": {}}
```

- `src`: origin node id. Set by the origin and never changed.
- `dst`: destination node id, or `*` for a flood to every node.
- `id`: 16 random hex characters, unique per origin. Dedupe key is `(src, id)`.
- `ttl`: hop limit. Default 16, maximum 64. Decremented on each forward; a
  packet with `ttl` 0 is not forwarded.
- `kind`: what the payload means. Defined later: `ping`, `pong`, `announce`
  (phase 03), `login-start`, `login-proof`, `token` (phase 04), `channel`,
  `membership`, `history` (phase 05), `file` (phase 07). Unknown kinds are
  forwarded, not handled.
- `payload`: JSON object, interpreted by `kind`.

Nodes forward a packet on every link except the one it arrived on, if its
`(src, id)` is new. A `dst` that equals the local node id is delivered and
not forwarded further, unless `dst` is `*`.

Example, a directed packet from `gentoo-planner` to `ubuntu-worker` across
two hops:

```json
{"type": "packet", "src": "gentoo-planner", "dst": "ubuntu-worker", "id": "9d02e7a1b54c3f60",
 "ttl": 15, "kind": "ping", "payload": {"sent": "2026-11-10T12:00:00Z"}}
```

### Limits

- Line length 1 MiB.
- `ttl` maximum 64.
- The dedupe cache holds recent `(src, id)` pairs. Its size and expiry are set
  in the routing phase.

## Storage

- Channel history is stored per node in `AGENTIPC_HOME` (default `~/.agentipc`),
  as implemented in `agentipc/store.py`. Mesh history sync adds only messages
  the local store lacks (phase 05).

## Open decisions

- Key distribution for encrypted virtual servers: one key per server, or per
  channel with rekeying. The authority can read messages either way.
- Login token verification: shared key, or a routed `verify` request to the authority.
- Newest PyNaCl release that still supports Python 2.7.
- Android libsodium binding, so message encryption matches across platforms.
- Python 2.7 verification of the client (needs an interpreter).
