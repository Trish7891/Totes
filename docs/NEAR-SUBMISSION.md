# NEAR Hackathon — submission pack

Deadline **2026-11-14 23:59 UTC**. Two tracks, both selectable: **AI Security &
Defense** (`security`) and **Agent Economy** (`agents`).

Everything below is copy-paste. Anything marked **YOU** is human-gated: I won't
create or fund a wallet, sign transactions, post on X, or submit forms on your
behalf.

---

## Project name

SafeCell

## One-liner (repo / social)

> A sandbox where an AI agent does real work and cannot leave the room it does it
> in — every run receipt is anchored on NEAR, and the spend policy is enforced on
> chain.

## Short description — paste into the form (1,214 chars, limit 2,000)

SafeCell runs AI-written code inside a sealed, disposable cell: new PID/IPC/net
namespaces, no host filesystem, no network, read-only system libs, private tmpfs,
CPU/memory/fork/file-size limits, and a wall-clock kill. You describe a task, the
planner writes Python, the cell executes it, and what you read on screen is real
executed output, never a summary.

The interesting part is what NEAR adds. Each run ends with a hash-chained
receipt. That chain is tamper-evident but not tamper-proof — whoever holds the
database could rewrite it. So the head of every run is anchored to a NEAR
contract, which makes the sequence of heads public and append-only. The same
contract enforces the agent's spend policy: a per-call limit, a daily limit, and
an action allowlist, checked on chain rather than in the agent's own process.

The agent account holds a function-call key scoped to `anchor()` only. It cannot
transfer, deploy, or re-key. A fully compromised host can burn gas; it cannot
move funds.

`GET /api/runs/{id}/proof` returns three things: the local head, the contract's
receipt for that run, and the contract's own head, plus a `match` flag.

Live: https://192-248-147-190.sslip.io — no signup, testnet only.

## How it uses NEAR (judges' technical section)

- **A policy contract, not a log** — `policy.safecell-neartest2.testnet` stores
  `{owner, agent, per_call_limit, daily_limit, allowlist}` and reverts with
  `NotAllowed`, `PerCallLimitExceeded`, or `DailyLimitExceeded` before any anchor
  is written. Enforcement is on chain; the agent cannot talk its way past it.
- **Append-only receipts** — `anchor(execution_id, receipt_hash, action, cost)`
  appends `{seq, prev_hash, receipt_hash, action, cost, agent, timestamp}`. The
  contract's `head` is the anchor every client compares against.
- **Function-call access keys** — the agent signs with a key whose permission is
  `FunctionCall{receiver_id: policy, method_names: ["anchor"], allowance}`. This
  is the least-privilege primitive the whole design rests on; a full-access key
  would undo it.
- **Three-way verification** — local chain head == contract receipt for this run
  == contract head. Any one disagreeing sets `match=false`.

## Track fit

- **AI Security & Defense** (primary): contained execution, explicit permission
  manifest, least-privilege key, on-chain spend limits. The threat model is the
  agent itself.
- **Agent Economy** (secondary): an agent that can pay per call needs a spend
  ceiling that survives a compromised host and a receipt trail a counterparty can
  audit without trusting the operator's database. The anchor chain is that trail.

## Demo video

`docs/video2/demo-near.mp4` — 76 s, narrated, recorded against the live
deployment. Shows: task → real python → real digest → anchor appears with
`match=true` → the same transaction open on NEAR Blocks testnet (`Call anchor`,
`agent3… → policy…`, status Success).

## X post (YOU post this; I don't post)

Attach `demo-near.mp4`. Rules require the words **NEAR HACKATHON** in the post.

```
NEAR HACKATHON entry — SafeCell

An AI agent that does real work but can't leave the room: sealed cell, no network,
no host filesystem. Every run's receipt head is anchored to a NEAR contract, and
the spend policy (per-call cap, daily cap, allowlist) is enforced on chain.

The agent's key is function-call-scoped to anchor() only — compromised host can
burn gas, can't move funds.

76s demo ⬇️  live: https://192-248-147-190.sslip.io
#NEARHackathon #AI #security
```

## Submission form — answers

- **Project name:** SafeCell
- **Team lead:** Trishan Rajkumar
- **Email:** **YOU** (the address you want judge mail at)
- **Team size:** 1
- **Tracks:** AI Security & Defense, Agent Economy
- **Repo:** https://github.com/Trish7891/Totes
- **Live demo:** https://192-248-147-190.sslip.io
- **Video:** the X post above, or the file in `docs/video2/demo-near.mp4`
- **NEAR account (payout):** **YOU** — a mainnet account you control
  (`wallet.near.org`, e.g. `trishan.near`). I will not create, fund, or sign for
  it.
- **Description:** the 1,214-char block above.

## Disclosure (already in the README, keep it)

The sandbox and the local hash chain existed before the hackathon window. The
NEAR integration — policy contract, anchor chain, function-call key scoping,
proof endpoint, and the UI proof line — was built inside it. Tagged:
`pre-hackathon` … `hackathon-submission`.

## Checklist

- [x] Public repo with MIT licence
- [x] README "do X → see Y" judge steps (`Try it in 60 seconds`)
- [x] Live demo, no signup, testnet only
- [x] Narrated video with a real anchor transaction on screen
- [x] Baseline disclosed, diff linked
- [ ] **YOU:** create/confirm a mainnet NEAR account for payout
- [ ] **YOU:** post the X draft with the video attached
- [ ] **YOU:** submit the form before 2026-11-14 23:59 UTC
