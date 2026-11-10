# AgentIPC Android (phase 06)

**Not compiled and not run.** The development machine has no Android SDK, and
its `java` install is broken, so none of this has been built or tested. Treat it
as a sketch until it builds on a machine with the SDK.

## What is here

- `VsProtocol.kt`: the virtual-server line protocol (login, join, say, quit),
  matching `agentipc/client.py`.
- `Auth.kt`: PBKDF2-HMAC-SHA256 key derivation and login proof, matching
  `agentipc/auth.py`. The PBKDF2 loop is written out because `PBEKeySpec`
  mishandles non-ASCII passwords.
- `SshTransport.kt`: sshj connection to one node, running `agentipc bridge client`.
  ssh provides the encryption.
- `MainActivity.kt`: a minimal screen to log in, read a channel and post a line.

## Not done

- Encrypted virtual servers. The client refuses them, because it needs a
  libsodium SecretBox binding. `lazysodium-android` is listed in the build file
  but no code uses it yet.
- Background service, notifications and settings storage. The app holds one
  connection while the screen is open.
- File transfer.
- Host key UI. `known_hosts` must be filled in by hand for now.
- The node host, key path and ssh user are hard-coded placeholders in
  `MainActivity.connect()`.

## To verify before building

- Dependency versions in `app/build.gradle.kts` (sshj, lazysodium, Kotlin,
  AGP). They were not checked against current releases.
- sshj on Android usually needs BouncyCastle handled explicitly. Check the
  current sshj guidance before relying on it.
- The protocol tests. Run the Python end-to-end tests against the node first, then
  compare a Kotlin login with `agentipc vs --ssh ... read` on the same server.
