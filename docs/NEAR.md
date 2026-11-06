# On-chain receipts (NEAR testnet)

SafeCell already hash-chains every step of a run locally: each event commits to
its predecessor, so any edit to the middle of a transcript breaks the head. An
off-chain chain has one weakness — whoever holds the database can rewrite the
whole thing and produce a new, self-consistent head. Anchoring closes that: the
head is written to a NEAR contract, which makes the *sequence* of heads public
and append-only.

## What is on chain

`contract/` is a minimal Rust contract (`safecell-policy`) with two jobs:

1. **Spend policy.** `new(owner, agent, per_call_limit, daily_limit, allowlist)`
   records the limits and the actions an agent is allowed to declare. `anchor()`
   reverts with `NotAllowed` for an action outside the allowlist and with
   `PerCallLimitExceeded` / `DailyLimitExceeded` when the declared cost breaks
   the policy. The numbers are enforced by the contract, not by the app.
2. **Receipt chain.** Each `anchor(execution_id, receipt_hash, action, cost)`
   appends `{seq, prev_hash, receipt_hash, action, cost, agent, timestamp}` and
   rejects a `receipt_hash` that is already recorded, so a run can be anchored
   exactly once. `get_receipt` / `get_chain_head` / `verify_chain` read it back.

```
policy.safecell-neartest2.testnet
  per_call_limit   0.010 NEAR
  daily_limit      0.050 NEAR
  allowlist        exec:python3, exec:node, exec:shell
  agent3.safecell-neartest2.testnet   # function-call key scoped to anchor()
```

The agent account holds a **function-call key**: it can call `anchor` on this
one contract and nothing else — it cannot transfer, cannot deploy, cannot sign
for the owner. The contract address is the key's allowlist. If the host is
compromised, the worst it can do is burn the key's allowance (~2 NEAR of gas,
by design, and about 0.1 NEAR per anchor).

## What the app does

After a run finishes and its local chain verifies, `safecell/runner.py` writes
the head:

```
GET /api/runs/{id}/proof
{
  "local":    {"verified": true, "chain_head": "59941e8c...", "event_count": 11},
  "anchor":   {"status": "anchored", "tx_hash": "5Zf...", "seq": 3, "prev_hash": "..."},
  "onchain":  {"seq": 3, "execution_id": "ba2eeccfd4ba", "receipt_hash": "59941e8c...", ...},
  "contract": {"head": "59941e8c...", "chain_len": 3, "verify_chain": true},
  "match":    true
}
```

`match: true` means three independent things agree: the local event chain
verifies, the contract's stored receipt for this run carries the same head, and
the contract's own `head` is that hash. `GET /api/near` reports the policy, the
remaining daily allowance and the chain length.

Anchoring is bounded (15 s join) and never blocks or fails a run: if the RPC is
slow the proof reads `"status": "pending"` and the same dict updates in place
once the transaction lands — testnet RPC from a home connection takes ~25 s, so
this is the normal first read.

## Reproduce it

```bash
# deploy + provision (owner key signs; run once)
python scripts/near_bootstrap.py --name safecell-neartest2

# then, with the printed env vars exported:
curl -s localhost:8000/api/near | python3 -m json.tool
ID=$(curl -s -X POST localhost:8000/api/runs -H 'Content-Type: application/json' \
      -d '{"task":"Print the sha256 of the word safecell","preset":"default"}' \
      | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
sleep 30 && curl -s localhost:8000/api/runs/$ID/proof | python3 -m json.tool
```

Keys live in `.secrets/near-testnet.json` (gitignored, mode 600). The owner key
stays on the deployer; the app only ever gets the scoped agent key.

## Honest limits

- Testnet only. The same contract compiles for mainnet, but a mainnet deploy
  needs a funded account — that step is deliberately not automated here.
- Anchoring proves *a* receipt chain existed at a point in time; it does not
  prove the code inside the cell did what the transcript says. Containment is
  bubblewrap's job; the chain is evidence, not attestation.
- One anchor per run: re-running the same task produces a different head and a
  new receipt, which is what you want, but it means runs cannot be merged.
