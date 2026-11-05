# Submission copy (paste into lablab)

## Links

- Live demo: https://192-248-147-190.sslip.io/
- Demo video (84s, narrated): https://github.com/Trish7891/Totes/blob/main/docs/video/demo.mp4
- Slide deck (9 slides, 16:9): https://github.com/Trish7891/Totes/blob/main/docs/safecell-deck.pptx
- Repo: https://github.com/Trish7891/Totes

## Project title
SafeCell — real work, zero blast radius

## Short description (≤ 300 chars)
A web app where a model writes code to do your task and that code runs inside a
sealed, disposable cell: no host filesystem, no network, capped CPU/memory, every
run returning a hash-chained receipt. The agent does real work and cannot leave
the room it does it in.

## Long description

**The problem.** An agent that only chats is a demo; an agent that executes is a
product — and a liability. The moment a model can run code it can read your home
directory, call out to the internet, fill the disk or fork-bomb the host. Most
"agent" products solve this by asking you to trust them.

**What SafeCell does.** You describe a task. A model plans and writes a small
Python program. SafeCell dispatches that program into a fresh cell — its own PID,
IPC, UTS and network namespaces, a read-only system tree, private tmpfs
workspaces, and rlimits on CPU, address space, file size, process count and open
files, plus a wall-clock kill on the whole process group. Output is captured with
a hard byte cap and scrubbed for credential shapes before it reaches the browser.
The cell is destroyed when the step ends, so nothing carries over.

The agent is assumed hostile. Every action goes through a single executor
function, and there is no trusted bypass and no "unsafe mode": if bubblewrap is
missing, the service returns 503 and refuses to execute rather than failing open.

**Verification, not adjectives.** Every run emits an event log where each event
hashes the previous one, so the receipt's `chain_head` commits to the entire
history. `GET /api/runs/{id}/verify` re-derives the chain and returns 409 if it
has been tampered with. Judges — and users — can check the log instead of
trusting it.

**Declared, not implied.** Each run carries a permission manifest — schema
`safecell.manifest/v1` — listing granted capabilities (filesystem, network,
compute, environment, secret handling), denied ones with the reason, and the one
thing the boundary does *not* stop (kernel exploitation on a shared kernel). The
manifest is generated from the same policy object the sandbox enforces, so
declare-vs-enforce cannot drift, and its hash rides in the receipt.

**Evidence.** 25 containment and manifest tests, none of which mock isolation: host paths
absent, host write blocked and verified absent afterwards, TCP and DNS both
raising, host secrets never crossing into the cell, key-shaped output redacted,
CPU burner killed at the rlimit, sleeper killed by the wall clock, 1 GB allocation
refused under a 512 MB cap, held-open children tripping NPROC, ephemeral
workspaces, private PID namespace, and concurrent executions all starting.

**Why it is a product and not a demo.** The web UI streams the plan, the code, the
real output and the receipt live; policies are selectable (paranoid/strict/
default/demo) and the policy fingerprint is recorded in every receipt, so a run
can be tied to the exact limits it ran under. The architecture keeps the executor
behind one interface so the isolation backend can be upgraded to microVMs without
touching the orchestration layer.

## Built with
Python, FastAPI, bubblewrap (namespaces + rlimits), Server-Sent Events,
hash-chained JSON event log, and a model behind any OpenAI-compatible endpoint
via any OpenAI-compatible endpoint. The current deployment runs the
deterministic scripted planner, a deliberate zero-cost fallback, so the demo is
reproducible without an API key or a paid inference credit.

## Tags
agents, sandboxing, security, code execution, verification, infrastructure

## Notes for judges
- `CONTAINMENT.md` contains the captured test output.
- The demo video shows the containment moment: three escape attempts refused live.
- The service fails closed: no bubblewrap ⇒ no execution, deliberately.
