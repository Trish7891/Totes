# SafeCell

**An agent that does real work — and cannot leave the room it does it in.**

SafeCell is a web application where you describe a task, a model writes code to
accomplish it, and that code runs inside a sealed, disposable cell. The output
you see is real executed output, and every run ends with a hash-chained receipt
that proves the log you read is the log that ran.

Built for the **Vultr Agent Rush — Blast Radius Zero** track: containment is not
a feature we added, it is the product.

---

## Why

An agent that only chats is a demo. An agent that executes is a product — and a
liability. The moment a model can run code, it can read your home directory,
call out to the internet, fill the disk, or fork-bomb the host. SafeCell inverts
the usual trust model: **the agent is assumed hostile.** Everything it touches is
mediated by one function, `exec_in_cell`, and there is no trusted bypass.

## Architecture

```
                       ┌──────────────────────────────────────────┐
   browser ───────────▶│  FastAPI control layer (Vultr VM)        │
                       │  · run store   · SSE event stream        │
                       │  · hash-chained receipt per run          │
                       └───────────────┬──────────────────────────┘
                                       │  plan(task) → code
                       ┌───────────────▼──────────────────────────┐
                       │  Planner                                 │
                       │  · LLMPlanner  → Vultr Serverless        │
                       │    Inference (OpenAI-compatible)         │
                       │  · ScriptedPlanner → deterministic demos │
                       └───────────────┬──────────────────────────┘
                                       │  code (never executed in-process)
                       ┌───────────────▼──────────────────────────┐
                       │  CELL  (bubblewrap, per run, disposable) │
                       │  ├─ new PID / IPC / UTS / net namespace  │
                       │  ├─ no network  (--unshare-net)          │
                       │  ├─ host paths absent (/home /root /mnt) │
                       │  ├─ /usr /etc /lib read-only             │
                       │  ├─ private tmpfs /work + /tmp           │
                       │  ├─ rlimits: cpu, mem, fsize, nproc, ofd │
                       │  ├─ wall-clock kill (process group)      │
                       │  └─ env scrubbed to an allowlist         │
                       └───────────────┬──────────────────────────┘
                                       │  stdout / stderr (redacted, capped)
                       ┌───────────────▼──────────────────────────┐
                       │  Verifier → receipt {chain_head, policy} │
                       └──────────────────────────────────────────┘
```

## Containment model

| Property | Mechanism | Proof |
|---|---|---|
| No host filesystem | Only `/usr /lib /bin /etc` bind-mounted read-only; `/home`, `/root`, `/mnt` never bound | `test_host_home_invisible`, `test_cannot_write_to_host` |
| No egress | `--unshare-net`, so no routes and no resolver | `test_network_egress_blocked`, `test_dns_blocked` |
| No credential leakage in | `--clearenv` + allowlist; host env never passed | `test_host_secrets_not_in_env` |
| No credential leakage out | Output scrubbed for key-shaped strings before it leaves | `test_secret_scrubbed_from_output` |
| Bounded CPU | `RLIMIT_CPU` | `test_cpu_burner_is_killed_by_cpu_limit` |
| Bounded wall clock | process-group SIGKILL on timeout | `test_wall_clock_timeout_kills_sleeper` |
| Bounded memory | `RLIMIT_AS` | `test_memory_limit` |
| No fork bomb | `RLIMIT_NPROC` expressed as headroom above ambient task count | `test_process_spawn_limit` |
| Bounded disk | `RLIMIT_FSIZE` + tmpfs workspace | `test_output_truncated` |
| Disposable state | Fresh tmpfs per run, destroyed after | `test_workspace_is_ephemeral` |
| Process isolation | PID namespace owned by the cell | `test_pid_namespace_isolated` |
| Tamper-evident history | Each event hashes the previous one | `test_policy_fingerprint_recorded` + `/api/runs/{id}/verify` |

All 16 tests run against the real sandbox — none of them mock isolation. See
[CONTAINMENT.md](CONTAINMENT.md) for the captured output.

## Quickstart

```bash
git clone git@github.com:Trish7891/Totes.git safecell && cd safecell
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
sudo apt-get install -y bubblewrap        # the only system dependency

python -m safecell.cli doctor             # is real isolation available?
python -m safecell.cli demo               # runs the containment demo, live
python -m unittest tests.test_containment # 16 containment assertions

uvicorn safecell.api:app --host 0.0.0.0 --port 8000   # web UI on :8000
```

If `bubblewrap` is missing, the service **refuses to execute** rather than
failing open (`503` with an explanation). There is no "unsafe mode".

## Using a real model

The planner speaks any OpenAI-compatible endpoint. Point it at Vultr Serverless
Inference (or Ollama locally):

```bash
export SAFECELL_LLM_BASE_URL="https://api.vultrinference.com/v1"
export SAFECELL_MODEL="<model-id>"
export SAFECELL_API_KEY="<key>"
```

With no model configured, the deterministic `ScriptedPlanner` handles three
built-in tasks so the product is always demonstrable end to end — the demo can
never fail because a model was slow.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/runs` | `{task, preset, planner}` → `{id}`; dispatches a worker |
| `GET` | `/api/runs/{id}` | Status, answer, steps, receipt |
| `GET` | `/api/runs/{id}/events` | SSE stream (replays history, then follows live) |
| `GET` | `/api/runs/{id}/verify` | Re-verifies the event chain; `409` if tampered |
| `GET` | `/api/health` | Sandbox availability + planner backend |

Presets: `paranoid`, `strict` (default), `default`, `demo`.

## Vultr deployment

The control layer is a Vultr VM; the model calls go to Vultr Serverless
Inference. Nothing about the design assumes a laptop.

1. Create the VM (Ubuntu 24.04, 2 vCPU / 4 GB is ample), open 80/443.
2. `apt install bubblewrap python3-venv`, clone, `pip install -r requirements.txt`.
3. Set the `SAFECELL_*` env vars, then run the systemd unit in [`deploy/safecell.service`](deploy/safecell.service).
4. Put Caddy or nginx in front for TLS (`deploy/Caddyfile`).
5. `curl /api/health` should report `"available": true`.

Because cells share the VM's kernel, run SafeCell on a VM with nothing else on
it — and see *Limitations* before treating it as a hard multi-tenant boundary.

## Demo video shot list (3 min)

1. **0:00** Ask for the CSV analysis; show real computed numbers and the receipt.
2. **0:40** Ask the agent to escape: host secrets, network, host write.
3. **1:20** The containment moment — `/etc/shadow: PermissionError`,
   `/home: FileNotFoundError`, `EGRESS: BLOCKED`, and nothing on the host.
4. **2:00** `python -m unittest tests.test_containment -v` — 16 green.
5. **2:30** `curl /api/runs/{id}/verify` → `chain_verified: true`.

## Limitations (stated plainly)

- Cells isolate via kernel namespaces, so a kernel bug is a shared boundary.
  For hostile multi-tenant use, run each cell in a microVM (Firecracker) — the
  `exec_in_cell` interface was written so the backend can be swapped without
  touching the runner.
- The scripted planner is a deterministic fallback for demos, not a general
  planner; quality on arbitrary tasks depends on the configured model.
- Single-process run store: restarting the service drops run history. Persisting
  receipts to disk is the obvious next step.

## Licence

MIT — see [LICENSE](LICENSE).
