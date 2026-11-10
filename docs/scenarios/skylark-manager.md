# Scenario: S1 plans, S2 builds, over SKYLARK-MANAGER

Two agent sessions on one computer (A) work through a plan together.

- **S1** is the planner. It writes the plan and the tasks, directs S2, and checks the reports.
- **S2** is the worker. It takes one task at a time, does the work, and writes a report.
- The virtual server is encrypted and runs on A. Both sessions join the channel `SKYLARK-MANAGER` on the server `skylark`.
- Bulk content lives in files under `~/skylark`. Chat carries only a command word, a file path and a short sentence.

Both sessions may poll (`read`) or block until a message arrives (`wait`). The work ends when S1 has checked every step and S2 has implemented the plan.

## 1. Set up the virtual server node (computer A)

This runs a separate node instance in `~/.agentipc-skylark`, so an existing mesh node on A is not touched. It needs PyNaCl for encrypted servers.

```sh
mkdir -p ~/.agentipc-skylark ~/skylark/tasks ~/skylark/reports
cat > ~/.agentipc-skylark/config.json <<'EOF2'
{"node": "skylark-a", "authority": "skylark-a", "listen": 7440, "control_port": 7441, "allow": ["127.0.0.1"]}
EOF2
read -r -s -p "password for s1: " S1_PW; echo; export S1_PW
read -r -s -p "password for s2: " S2_PW; echo; export S2_PW
agentipc --home ~/.agentipc-skylark account add s1 --password-env S1_PW
agentipc --home ~/.agentipc-skylark account add s2 --password-env S2_PW
agentipc --home ~/.agentipc-skylark server create skylark --encrypted
nohup agentipc --home ~/.agentipc-skylark node run > ~/.agentipc-skylark/node.log 2>&1 &
```

Start the node only once. S1 and S2 run as the same Unix user on this computer, so they share `~/.agentipc` and every other default path. Only the accounts `s1` and `s2` separate them. Before you start the node, check that it is not already running:

```sh
pgrep -fa "agentipc-skylark node run" || echo "not running"
```

Check that the node answers. This should print nothing and exit 0:

```sh
agentipc vs --direct 127.0.0.1:7440 --server skylark --user s1 --password-env S1_PW read SKYLARK-MANAGER
```

**Separate homes.** This computer's default home, `~/.agentipc`, belongs to the mesh node. Both agent sessions must keep off it. Commands that use a home (`node`, `init`, `join`, `leave`, `say`, `read`, `who`, `channels`, `account`, `server`) get `--home ~/.agentipc-skylark` in these sessions, or run after `export AGENTIPC_HOME=$HOME/.agentipc-skylark`. The `vs` commands below need no home and do not read one.

Export `S1_PW` in the S1 session's shell, and `S2_PW` in the S2 session's shell, before starting each agent.

If the virtual server is on another computer B instead, run the same setup on B and use `--direct B:7440` in place of `127.0.0.1:7440` on both sessions. B's node must allow A's address in its `allow` list.

## 2. Message for S1 (the planner)

Paste this into the first agent session, after `S1_PW` is exported.

```text
You are S1, the planner. You own the plan and you direct S2, the worker.
Read all of this before you act.

Setup
- Work directory: ~/skylark (tasks/, reports/, plan.md, final.md).
- Chat: encrypted channel SKYLARK-MANAGER on server skylark.
- Do not start a node, and do not run any agentipc command with a home unless it has `--home ~/.agentipc-skylark`. Your login is the account s1 and nothing else.
- Run this to speak and listen; SK stands for it:
    SK="agentipc vs --direct 127.0.0.1:7440 --server skylark --user s1 --password-env S1_PW"
- Post a message:  $SK say SKYLARK-MANAGER "TASK tasks/001-short-name.md"
- Read the channel: $SK read SKYLARK-MANAGER
- Block until S2 writes something: timeout 1800 $SK wait SKYLARK-MANAGER --from s2

Rules
1. Put all detail in files. A chat message is a command word, a file path and a short sentence, and nothing more. Never paste report text into chat.
2. Write plan.md as numbered steps. Each step is one task.
3. For step N, write tasks/NNN-short-name.md with: the goal, the files to touch, the acceptance check, and the report path reports/NNN.md.
4. Post  TASK tasks/NNN-short-name.md  and wait for S2.
5. Reply to what S2 posts:
   - ACK <path>: S2 has the task. Wait again.
   - DONE reports/NNN.md <sentence>: read the report, then run the acceptance check yourself. If it passes, mark the step done in plan.md and move to the next step. If it fails, write a fix task and post TASK for it.
   - BLOCKED reports/NNN.md <sentence>: read the report and decide. Write a new task or change the plan.
6. If nothing arrives within 30 minutes of a TASK, post the same TASK again and wait again.
7. When every step in plan.md is done and checked, write final.md with a short summary and the list of reports. Post:  FINISHED plan.md
8. Do not stop before step 7, unless S2 is blocked and you cannot resolve it. In that case post STOP and say why in one sentence.
```

## 3. Message for S2 (the worker)

Paste this into the second agent session, after `S2_PW` is exported.

```text
You are S2, the worker. S1 gives you tasks. You do them, write a report for each, and tell S1 where the report is.
Read all of this before you act.

Setup
- Work directory: ~/skylark (tasks/, reports/).
- Chat: encrypted channel SKYLARK-MANAGER on server skylark.
- Do not start a node, and do not run any agentipc command with a home unless it has `--home ~/.agentipc-skylark`. Your login is the account s2 and nothing else.
- Run this to speak and listen; SK stands for it:
    SK="agentipc vs --direct 127.0.0.1:7440 --server skylark --user s2 --password-env S2_PW"
- Post a message:  $SK say SKYLARK-MANAGER "DONE reports/001.md tests pass"
- Read the channel: $SK read SKYLARK-MANAGER
- Block until S1 writes something: timeout 1800 $SK wait SKYLARK-MANAGER --from s1

Rules
1. Put all detail in files. A chat message is a command word, a file path and a short sentence, and nothing more. Never paste report text into chat.
2. First, catch up: read the channel and note the newest TASK whose report is not written yet.
3. Then loop:
   a. Block with the wait command above. It prints one message and exits. If it prints nothing, the 30 minutes ran out: wait again.
   b. On TASK tasks/NNN-name.md: post  ACK tasks/NNN-name.md  Then read the task file and do the work it describes.
   c. Write the report to reports/NNN.md: what you changed, the files, and the result of the acceptance check.
   d. Post  DONE reports/NNN.md <one sentence>  if the acceptance check passed, or  BLOCKED reports/NNN.md <one sentence>  if it did not and you cannot fix it.
   e. Go back to 3a.
4. If a TASK arrives again for a report that already exists, check the report. If the work is done, post DONE for it again. Do not redo the work.
5. Stop when S1 posts FINISHED plan.md or STOP. Then post nothing else.
```

## 4. How the loop works

- **Waiting.** `wait` joins the channel, skips the history, and prints the first live message from the given sender. It runs until that message arrives. Wrap it in `timeout` to set a deadline. The exit status is 0 when a message arrives.
- **Polling.** `read` prints the whole history. Use it at startup to catch up, or any time you want to check the channel without blocking.
- **Missed messages.** A message posted between a `read` and the next `wait` is not printed by `wait`. The 30-minute rule covers that: S1 posts the same TASK again, and S2 handles a repeated TASK by checking for its report first (step 4 in S2's rules).
- **Encryption.** The node stores the message bodies as ciphertext. Only the two sessions hold the key, which is derived from their passwords.

## 5. Files

```text
~/skylark/
  plan.md              S1: numbered steps, with done marks
  tasks/NNN-name.md    S1: goal, files, acceptance check, report path
  reports/NNN.md       S2: what was done and how it was checked
  final.md             S1: summary when the plan is finished
```

## 6. Checking the run

- The plan is finished when `final.md` exists and `FINISHED plan.md` is in the channel.
- Each step in `plan.md` is marked done, and each mark has a report behind it.
- `$SK read SKYLARK-MANAGER` shows only short lines with paths, and no report text.

## Known limits

- `wait` returns after one message, so each agent calls it again after every message it handles.
- Both agents use one account each. Messages show the account name, so `--from s1` and `--from s2` tell them apart.
- A node restart does not lose stored messages, but a session that is blocked in `wait` must be started again.
