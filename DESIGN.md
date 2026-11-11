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

- A link is protected by its transport. ssh links are encrypted by ssh.
  Direct plain links are allowed only on the LAN, from addresses the accepting
  node lists explicitly. They never cross the public internet.
- Links may also be encrypted by the project itself (planned, see "Link
  transports"). The project's own link crypto uses PyNaCl. That is allowed on
  every platform that PyNaCl supports, including Python 2.7.
- The shell commands feature (below) is off unless a node enables it.
- Login is challenge-response (HMAC over pbkdf2-derived keys). The password is
  never sent.
- Encrypted virtual servers encrypt message bodies end to end with PyNaCl
  (libsodium). PyNaCl is optional on Python 2.7. Unencrypted servers do not
  need it.

## Wire protocol (version 2)

Applies to ssh links and direct TCP links alike. Version history and the
compatibility rules are in "Versioning" below.

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
only after both `hello` frames are seen and their version ranges overlap.

```json
{"type": "hello", "node": "gentoo-planner", "version": 2, "min_version": 2,
 "link": "ssh", "features": ["core", "vs", "shell", "file"]}
```

- `node`: sender's node id.
- `version`: highest wire version the sender speaks.
- `min_version`: lowest wire version the sender accepts. Missing means `version`.
- `link`: `ssh` or `direct`. Informational; the receiver checks the allowlist itself.
- `features`: what the sender can do. Informational, a list of names. `core` is
  required; a node without a feature must not be sent packets of that kind on
  purpose (a small node without `vs` gets no virtual-server packets).

The link runs at the highest version both sides speak. If the ranges do not
overlap, the link is closed with `bye` reason `version`.

A node that receives its own node id in `hello` sends `bye` with reason
`self-link` and closes.

### `bye`

Closes the link. Sent before closing, when possible.

```json
{"type": "bye", "reason": "version"}
```

Reasons used: `version`, `bad-frame`, `self-link`, `not-allowed`
(direct link from an address not on the allowlist), `shutdown`.

### `packet` (routed envelope)

Every routed message, in any phase, uses this envelope.

```json
{"type": "packet", "src": "gentoo-planner", "dst": "*", "id": "3f9a1c0e7b2d4a58",
 "ttl": 16, "kind": "ping", "payload": {}}
```

- `src`: origin node id. Set by the origin and never changed.
- `dst`: destination node id, a list of 1 to 64 distinct node ids (one packet
  fans out to all of them, one copy per outgoing link), or `*` for a flood to every node.
- `id`: 16 random hex characters, unique per origin. Dedupe key is `(src, id)`.
- `ttl`: hop limit. Default 16, maximum 64. Decremented on each forward; a
  packet with `ttl` 0 is not forwarded.
- `kind`: what the payload means. Kinds in use:
  - routing and ping: `announce` (topology, flooded), `ping` and `pong` (to every known node)
  - login: `login-start`, `login-challenge`, `login-proof`, `login-result`
  - token check: `verify`, `verify-result`
  - virtual server: `membership` (flooded join and part), `channel` (flooded message),
    `sync` (flooded on every new link: membership plus what the sender has), `history` (directed reply)
  - files: `file` (one chunk, directed to the receiving node)
  - shell: `exec` (command request, carries the client token), `exec-result` (reply)
  Unknown kinds are forwarded, not handled.
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

## Link transports

Two nodes can be joined in several ways. A node may offer any of them, and
configuration picks the ones it dials or accepts.

| Transport | Status | Use |
|---|---|---|
| ssh session | done | default for any machine with ssh |
| direct plain TCP, allowlisted addresses | done | retro LAN machines, py2.7 |
| direct TCP with PyNaCl link encryption | planned (phase 10) | LAN or WAN machines without ssh; py2.7 with PyNaCl |
| obfuscated HTTP/HTTPS | note only | networks with tight policies, where only web traffic passes |

Obfuscated HTTP/HTTPS would tunnel link frames inside ordinary-looking HTTP(S)
requests, so that the link looks like web traffic to a policy filter. It needs
its own design: request shape, polling or streaming, timing, and TLS through a
proxy. It is not started.

## Remote shell commands

A node runs shell commands only if its config enables them:

```json
"shell": {"enabled": true, "users": ["alice"]}
```

- Off by default. A node with no `shell` section refuses every command.
- A client logged in to a virtual server asks a node to run a command with
  `EXEC NODE TIMEOUT COMMAND`. The request carries the client's token.
- The target node asks the authority to verify the token, then checks the user
  against its own `users` list. Only listed users run commands there.
- Commands run in a worker thread through the local shell, with a timeout,
  and output is cut to 64 KiB per stream.
- Plain servers only. Encrypted servers refuse EXEC, because the command text
  would be visible to relay nodes.

This lets agents use machines that cannot run agents well. An agent on a
capable machine runs commands on the weaker one.

## Planned: small C node (top priority)

A C node with only plain links, remote shell commands and file transfer. It is
for hosts too old or too small for Python. Targets:

- linux-ppc64be, linux-ppc32be, macos-ppc32, morphos-ppc32, amigaos-1.3-m68k
- openwrt-mips32, linux-arm32, freebsd-amd64, openindiana-amd64 (illumos)
- msdos 6.22 (Windows 95/98 era), Windows XP 32-bit, 16-bit MS-DOS
- 8-bit single-board projects (Z80, i8085)

Scope: the wire protocol v1 framing, `hello`, `packet` routing for direct
neighbours, `exec`, and file chunks. No virtual server and no authority.

## Planned: embedded library (idea)

The same node code, as a library that a host program links in (for example a
DAW such as Ardour, an image editor such as GIMP, or a browser such as
Chromium). The host exposes a virtual shell and a virtual filesystem, so an AI
agent can drive the program. Not designed yet.

## Versioning

The wire protocol and the virtual-server client protocol are both versioned.
Nothing promises that the protocol stays the same.

### Rules

- **Breaking change:** anything that changes what an existing frame, field or
  kind means, or removes one. It needs a new version number.
- **Compatible change:** a new optional field, or a new packet kind that old
  nodes forward and ignore. These stay in the current version. Receivers already
  ignore unknown fields and forward unknown kinds.
- **Negotiation:** each side announces `version` and `min_version`. The link
  runs at `min(version_a, version_b)`, and is refused if that is below either
  side's `min_version`.
- **Dropping support:** a node raises its `min_version` only when it no longer
  needs the old behaviour. Until then, old and new nodes keep talking.
- **C node:** speaks the same versions. It implements exactly the versions it
  advertises, and states its `features`.

### Wire version history

- **1:** the first wire format. Single-destination packets only. Not spoken by
  any live node after this change.
- **2:** `dst` may be a list of 1 to 64 distinct node ids (multicast). `hello`
  carries `version`, `min_version` and `features`. Current.

### Client protocol (virtual server line protocol)

The `VERSION N` command is optional. A client that sends it gets
`VERSION M`, where M is the highest version both sides speak. A client that
sends nothing gets version 1 behaviour. An unsupported request gets
`ERR version`. Current client version: 1.

## Implementation

- `agentipc/proto.py`: framing, link frames, routed packet envelope.
- `agentipc/link.py`: one link (reader and writer threads, outgoing queue).
- `agentipc/routing.py`: topology (link state), next hops, ping results, dedupe cache.
- `agentipc/node.py`: links, dispatch, forwarding, announce, ping, tick loop.
- `agentipc/daemon.py`: control socket on 127.0.0.1, direct listener with allowlist,
  ssh and direct dialers with backoff.
- `agentipc/auth.py`: accounts, challenge-response login, tokens, verify cache.
- `agentipc/vserver.py`: channels on the mesh, membership, history sync, client protocol.
- `agentipc/client.py`: client side: ssh or direct connection, login, read, say, upload.
- `agentipc/crypto.py`: PyNaCl SecretBox, imported only for encrypted servers.
- `agentipc/files.py`: chunked file transfer with out-of-order assembly and SHA-256.
- `agentipc/shell.py`: remote shell commands, opt-in per node.

Process model: `agentipc node run` is the daemon. Remote ssh sessions run
`agentipc bridge link` (peer links) or `agentipc bridge client` (client
sessions), which pipe stdin and stdout to the daemon's control socket. The
local control socket listens on 127.0.0.1 only and needs a secret. The secret
is random, written to `control.token` in the node's home with mode 0600, and
sent after the mode: `MODE link SECRET` or `MODE client SECRET`. Other local
users cannot read it, so they cannot open links or client sessions. The direct
listener is not affected: it uses the allowlist instead.

## Retention

`retention_days` in a node's config keeps channel records for that many days
(off when unset). Each node drops older records at startup and hourly, and refuses
older records when a peer sends them again, so a pruned record does not come back
through sync. Membership is not pruned: it is current state.

## Feature gate
Announces carry the node's `features`. Before a unicast send, the sender skips
any node that announced its features and lacks the one the kind needs
(`proto.KIND_FEATURES`). A refused send reports `ERR no-route`. Nodes that
announce no features are not refused.

## Decisions taken during implementation

- **Link crypto:** allowed. The earlier "no custom crypto" rule is withdrawn.
  Planned link encryption uses PyNaCl (phase 10).
- **Key distribution:** one key per encrypted virtual server, created by the
  authority. At login the key is wrapped with the user's password-derived key
  (SecretBox), so relay nodes never see it. The authority holds every key, so it
  can read every encrypted message. Rekeying on membership change is not done.
- **Token verification:** a node verifies a token by a routed `verify` request
  to the authority, and caches the answer until expiry. No shared key.
- **Tokens and encrypted servers:** a token proves identity only. An encrypted
  server requires a password login, because the key is wrapped under the password.
- **Login:** the password never travels. The authority sends a salt and a nonce,
  and the client returns an HMAC. Unknown users get a stable fake salt, so login
  does not reveal which accounts exist.
- **Membership and history:** joins and parts are flooded. Every new link
  triggers a flooded `sync` carrying the sender's local members and the highest
  sequence number it holds per origin. Receivers reply with the records that the
  sender lacks. Gaps in a sequence are not repaired.
- **Node loss:** when a node's announcements expire, its members are removed.
- **File transfer:** supported on plain servers only. Encrypted servers refuse it
  for now.
- **PyNaCl:** 1.4.0 is the last release that supports Python 2.7. Python 2.7
  nodes and clients without PyNaCl can use plain servers, direct links and ssh.

## Open points

- Python 2.7 is not verified: no interpreter was available. Code avoids
  Python 3-only syntax, but it needs a real run.
- Android app: specified (phase 06) but not built. There is no Android SDK on
  the development machine.
- Encrypted file transfer.
- Record and membership storage grows without bounds.
