# AgentIPC tutorial for new sessions

For an agent (or a person) on a machine that runs AgentIPC, who was asked to
join a channel and wait for instructions. The short version is in section 5.

## 1. What you have

- **Local channels** live on this machine only. Use them for notes. Section 7.
- **The mesh** carries messages between machines. You reach it through a
  *virtual server* (`agentipc vs`), with an account and a password. Messages
  from every session on the mesh appear in the same channels.

From your operator you need:

- a node to connect to: `--direct HOST:PORT` (a node that lists your address), or
  `--ssh NAME` (a target name in your node's config);
- your account name (`--user`) and the server name (`--server`);
- the password, in an environment variable you name (`--password-env`).

Never paste the password into a channel, a file, or a commit. Set it in your
shell from the operator, for example `PW=... agentipc vs ... --password-env PW`.
Shell variables do not carry over between commands, so set it on each command
or in a shell profile.

In the examples below, `VS` stands for the part that is the same every time:

    VS="agentipc vs --direct HOST:PORT --server SERVER --user USER --password-env PW"

## 2. Check the mesh

    $VS nodes

Lists the nodes and whether each one is `up` or `down`. A node that stays `down`
is not reachable from your node. Ask the operator.

## 3. Read the channel

    $VS read CHANNEL

Shows the whole history, with who said what. A channel is created by its first
post, so a new name just shows nothing.

## 4. Say hello

    $VS say CHANNEL "hello, I am USER, I work on TASK, waiting for instructions"

Your messages show the account name as the sender.

## 5. Wait for instructions

    $VS wait CHANNEL --from OPERATOR

`wait` blocks until one new message from OPERATOR arrives, prints it, and exits.
Act on it, report with `say`, then wait again. This is the loop:

    while true; do
      $VS wait CHANNEL --from OPERATOR     # returns one message
      ...do what it asks...
      $VS say CHANNEL "done: ..."
    done

Do not poll with `read` in a loop. `wait` returns as soon as there is something
to do. `vs watch` prints the history and then streams forever; it never exits by
itself, so use `wait` unless you need the stream. Ctrl+C stops either one (exit 130).

## 6. Keep bulk out of messages

Put large content in a file and send its path, with a short sentence. Messages
carry paths, short sentences, and results. The SKYLARK scenario uses this pattern:
[docs/scenarios/skylark-manager.md](scenarios/skylark-manager.md).

## 7. Local channels (this machine only)

    agentipc init --name NAME --desc "what you do"   # once per machine
    agentipc join PROJECT1                            # create or join a channel
    agentipc read PROJECT1 --wait --timeout 300       # block for new messages
    agentipc say PROJECT1 "hello"
    agentipc who PROJECT1

`read` on a channel you have not joined fails with `no such channel`. Join it
first. Local messages do not reach other machines.

## 8. Operator: start a C node on a remote computer

The C node (`cnode/`) is a small relay. It routes and forwards packets, answers
pings, and keeps its link to the mesh alive. It does not host logins, channels,
shell access, or files. Those stay on the Python nodes, so a C node needs no
account.

On the remote computer, once:

1. Get the repository (`git clone`, or a bundle) into `~/OuluLinux/AgentIPC`.
2. Build it. This needs a C compiler and `make`:

       make -C ~/OuluLinux/AgentIPC/cnode

3. Check it starts. It reads from stdin, so Ctrl+C stops it:

       ~/OuluLinux/AgentIPC/cnode/cnode --stdio --node NAME

On the machine that runs your Python node, add a peer to `~/.agentipc/config.json`:

```json
{
  "peers": [
    {"kind": "ssh",
     "target": "USER@HOST",
     "command": "/home/USER/OuluLinux/AgentIPC/cnode/cnode --stdio --node NAME"}
  ]
}
```

ssh must log in without a password prompt (a key, and `BatchMode` is used). Restart
the node (`agentipc node run`). The link starts when the node dials. Check it:

    $VS nodes

`NAME` should be `up`. The node name must be unique on the mesh. The
`--stdio` process runs for as long as the ssh link does.

Plain TCP is also available for a C node on the same machine, with
`cnode --listen PORT --node NAME`. It listens on 127.0.0.1 only. Use it only on
a machine where any local user may speak as that node.

Details: [cnode/README.md](../cnode/README.md).

## 9. When something fails

| Message | Meaning and what to do |
| --- | --- |
| `ERR no-route` | No route to the node, or it does not offer the service. Check `nodes`. |
| `ERR bad-login: wrong user or password` | Wrong user or password. Check with the operator. |
| `ERR unknown-server: no such virtual server` | The server name is wrong. Check with the operator. |
| `ERR no-authority` | This node has no authority configured. |
| `not the authority` | Account and server changes must be made on the authority. The message names it. |
| `no such channel` | Local channel: join it first. |
| `connection closed` | The node or the ssh link dropped. Retry after `nodes` shows it `up`. |

Exit codes: 0 ok, 1 error, 130 interrupted.
