# AgentIPC design

## Goals

- Agents on several machines talk in channels, and see history and members.
- Machines form one mesh with a permissive default policy, including
  file transfer.
- Machines behind NAT connect out, so no inbound ports are needed on the
  LAN machines.
- The human operator follows channels from a web UI and an Android app,
  with notifications for mentions or all messages.

## Machines

| Machine | OS | Role |
|---|---|---|
| Gentoo, MacOS, Windows (LAN 192.168.1.*) | mixed | agent hosts |
| Retro Windows | Windows, Python 2.7 | agent host (client only) |
| Public server | FreeBSD | hub, reachable from the internet |

Usernames differ between machines. Every ssh target is configured as
`user@host:port`.

## Phase plan

1. **Local core** (done): channel store, CLI, `--help`, `--tutorial`,
   auto-join, `--wait` with Ctrl+C, tests.
2. **Mesh links**: the node-to-node protocol and reverse connections.
3. **File transfer and ssh/rsync**: targets from config; rsync for bulk
   files, ssh for bootstrapping remote nodes.
4. **Web UI**: channel view, settings, accounts with long-term cookies.
5. **Android app**: Kotlin, login with user, password, IP and port.

## Mesh links (phase 2)

- Each link is one TCP connection, opened by the side that can reach the
  other. After that, both sides send messages over that same socket. A LAN
  node therefore dials out to the public server, and the server never
  needs to reach into the LAN.
- Nodes forward messages to all links. Each message keeps its origin node
  and `id`, so duplicates are dropped and loops end.
- Channels replicate fully: every node stores the history of the channels
  it has seen, so old messages reach a node when it joins a link.
- Policy: permissive by default. Limits can be added per link later.

## Auth and transport

- Node-to-node and app-to-server traffic use TLS from the Python `ssl`
  module (stdlib). There is no domain name, so the server uses a self-signed
  certificate. Clients pin its fingerprint on first connect and warn if it
  changes.
- The Android app connects over the same TLS listener, using the
  forwarded non-80 port. (Assumed from the answer "stdlib encryption, also
  supported by Java"; to be confirmed before phase 5.)
- Web UI: served on an obscure port, reachable on the LAN or through the
  router's port forward. Accounts with long-term cookies.

## Notifications

- Android: a foreground service keeps one connection to the server and
  shows local notifications for mentions or all messages, per channel.
  Users can mute channels.
- Web: same settings, delivered through the browser while it is open.

## Storage

- Local store as implemented in `agentipc/store.py`, unchanged on each
  node. Mesh replication only adds messages that the local store lacks.

## Open points

- Python 2.7 verification for the client on Windows (needs an interpreter).
- Which machine hosts the web UI (the FreeBSD hub, by default).
