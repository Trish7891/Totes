#!/usr/bin/env python3
"""Provision SafeCell's NEAR testnet deployment in one pass.

Creates (or reuses) an owner account, two subaccounts — `policy.` for the
contract and `agent.` for the restricted signer — deploys the contract, and
writes the keys to `.secrets/near-testnet.json` (mode 600, gitignored).

    python scripts/near_bootstrap.py --name safecell-test-1

Everything is testnet. No mainnet, no funds, no wallet file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from safecell.near_client import (  # noqa: E402
    AccessKey, Account, NearError, PublicKey, Rpc, action_add_key,
    action_create_account, action_deploy, action_function_call, action_transfer,
    b58encode, outcome_summary, wait_for_tx,
)

WASM = Path(__file__).resolve().parents[1] / "contract/target/wasm32-unknown-unknown/release/safecell_policy.wasm"
SECRETS = Path(__file__).resolve().parents[1] / ".secrets/near-testnet.json"
HELPER = "https://helper.testnet.near.org/account"

NEAR = 10**24
PER_CALL = 10 * 10**21      # 0.010 NEAR declared at risk per execution
DAILY = 50 * 10**21         # 0.050 NEAR per UTC day
KEY_ALLOWANCE = 2 * 10**24   # total NEAR the anchor-scoped key may ever burn (gas + deposit)
ALLOWLIST = ["exec:python3", "exec:node", "exec:shell"]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def helper_create(name: str, pk: PublicKey) -> None:
    body = json.dumps({"newAccountId": name, "newAccountPublicKey": pk.str()}).encode()
    req = urllib.request.Request(HELPER, data=body, headers={
        "Content-Type": "application/json", "User-Agent": "safecell/1.0"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        log(f"helper {resp.status}: {resp.read()[:200].decode(errors='replace')}")


def account_exists(rpc: Rpc, account_id: str) -> bool:
    try:
        rpc.call("query", {"request_type": "view_account", "finality": "final",
                           "account_id": account_id})
        return True
    except NearError:
        return False


def send(rpc: Rpc, signer: Account, receiver: str, actions: list[bytes]) -> dict:
    result = rpc.send(signer, receiver, actions)
    summary = outcome_summary(result)
    if not summary["ok"]:
        raise NearError(f"tx failed: {json.dumps(summary['status'])[:300]} "
                        f"logs={summary['logs']}")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="safecell-test-1", help="owner account name (without .testnet)")
    ap.add_argument("--rpc", default="https://rpc.testnet.near.org")
    args = ap.parse_args()

    if not WASM.exists():
        log(f"missing wasm: {WASM}\nbuild with: cargo build --target wasm32-unknown-unknown --release")
        return 2

    rpc = Rpc(args.rpc)
    owner_id = f"{args.name}.testnet"
    policy_id = f"policy.{owner_id}"
    agent_id = f"agent.{owner_id}"

    existing = None
    if SECRETS.exists():
        existing = json.loads(SECRETS.read_text())
        if existing.get("owner_account") != owner_id:
            existing = None

    if existing and account_exists(rpc, owner_id):
        owner = Account.from_secret(owner_id, existing["owner_key"])
        log(f"reusing owner {owner_id}")
    else:
        import nacl.signing
        sk = nacl.signing.SigningKey.generate()
        owner = Account(owner_id, PublicKey(bytes(sk.verify_key)), sk)
        if account_exists(rpc, owner_id):
            log(f"{owner_id} exists but no key on disk — pick another --name")
            return 3
        log(f"creating {owner_id} via helper")
        helper_create(owner_id, owner.public_key)
        for _ in range(20):
            if account_exists(rpc, owner_id):
                break
            time.sleep(1.5)
        else:
            log("owner account did not appear; helper may be rate-limited")
            return 4
        log(f"owner live: {owner_id}")
        # Save the owner key immediately: if a later step fails, the account is
        # still recoverable instead of stranded with a key that only existed in RAM.
        SECRETS.parent.mkdir(parents=True, exist_ok=True)
        SECRETS.write_text(json.dumps({
            "network": "testnet", "owner_account": owner_id,
            "owner_key": "ed25519:" + b58encode(bytes(owner.signing_key)),
        }, indent=2) + "\n")
        os.chmod(SECRETS, 0o600)

    def policy_ready() -> bool:
        try:
            rpc.view(policy_id, "get_policy")
            return True
        except NearError:
            return False

    def key_allowance(account_id: str, pk: PublicKey) -> int | None:
        """Remaining allowance of a function-call key, or None if it is absent.

        A single anchor costs about 0.1 NEAR of allowance (gas + deposit), so a
        key minted with less than that is dead on arrival: an account can only
        add keys to itself, and a function-call key cannot re-key its owner.
        Rotation therefore happens at creation time, never afterwards.
        """
        try:
            ak = rpc.access_key(account_id, pk)
        except NearError:
            return None
        perm = ak.get("permission") or {}
        fc = perm.get("FunctionCall") if isinstance(perm, dict) else None
        if fc is None:
            return 1 << 62          # full-access key: nothing to top up
        allowance = fc.get("allowance")
        return (1 << 62) if allowance is None else int(allowance)

    if not policy_ready():
        log(f"creating {policy_id} + deploying contract")
        s = send(rpc, owner, policy_id, [
            action_create_account(),
            action_transfer(4 * NEAR),   # helper accounts start with 10 N
            action_deploy(WASM.read_bytes()),
            action_function_call("new", {
                "owner": owner_id, "agent": agent_id,
                "per_call_limit": str(PER_CALL), "daily_limit": str(DAILY),
                "allowlist": ALLOWLIST,
            }, gas=300_000_000_000_000),
        ])
        log(f"deploy tx {s['tx_hash']} gas={s['gas_burnt']}")
    else:
        log(f"{policy_id} already deployed")

    # The agent account must be born with its function-call key: NEAR lets an
    # account add keys only to itself, so a keyless subaccount is stranded.
    import nacl.signing
    saved_agent = (existing or {}).get("agent_key")
    if saved_agent and account_exists(rpc, agent_id) and (
            key_allowance(agent_id, Account.from_secret(agent_id, saved_agent).public_key)
            or 0) >= KEY_ALLOWANCE:
        agent = Account.from_secret(agent_id, saved_agent)
        log("reusing saved agent key")
    else:
        sk = nacl.signing.SigningKey.generate()
        agent_pk = PublicKey(bytes(sk.verify_key))
        free = [c for c in [agent_id] + [f"agent{i}.{owner_id}" for i in range(2, 9)]
                if not account_exists(rpc, c)]
        if not free:
            raise SystemExit("no free agent subaccount name (agent..agent8 taken)")
        agent_id = free[0]
        agent = Account(agent_id, agent_pk, sk)
        log(f"creating {agent_id} with a function-call key (anchor only)")
        s = send(rpc, owner, agent_id, [
            action_create_account(),
            action_transfer(NEAR // 2),
            action_add_key(agent_pk, AccessKey(
                nonce=0,
                function_call=(policy_id, ["anchor"], KEY_ALLOWANCE),
            )),
        ])
        log(f"agent tx {s['tx_hash']} gas={s['gas_burnt']}")

    onchain_agent = rpc.view(policy_id, "get_agent")
    if onchain_agent != agent_id:
        log(f"pointing contract at {agent_id} (was {onchain_agent})")
        s = send(rpc, owner, policy_id, [
            action_function_call("set_agent", {"agent": agent_id}, gas=50_000_000_000_000),
        ])
        log(f"set_agent tx {s['tx_hash']}")

    wasm_sha = hashlib.sha256(WASM.read_bytes()).hexdigest()
    SECRETS.parent.mkdir(parents=True, exist_ok=True)
    SECRETS.write_text(json.dumps({
        "network": "testnet",
        "owner_account": owner_id,
        "owner_key": "ed25519:" + b58encode(bytes(owner.signing_key)),
        "policy_account": policy_id,
        "agent_account": agent_id,
        "agent_key": "ed25519:" + b58encode(bytes(agent.signing_key)),
        "wasm_sha256": wasm_sha,
        "per_call_yocto": str(PER_CALL),
        "daily_yocto": str(DAILY),
        "created_ms": int(time.time() * 1000),
    }, indent=2) + "\n")
    os.chmod(SECRETS, 0o600)

    log(f"keys -> {SECRETS} (mode 600)")
    print(f"""
export NEAR_RPC_URL={args.rpc}
export NEAR_CONTRACT={policy_id}
export NEAR_AGENT_ACCOUNT={agent_id}
export NEAR_AGENT_KEY=$(python3 -c "import json;print(json.load(open('{SECRETS}'))['agent_key'])")
""")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except NearError as e:
        print(f"NEAR error: {e}", file=sys.stderr)
        raise SystemExit(1)
