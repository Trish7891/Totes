"""Anchor SafeCell receipts on NEAR.

The contract is the trust anchor, not this module: it enforces the spend policy
and refuses any anchor that does not extend the current chain head. Everything
here is a thin, best-effort client — a failed anchor is reported on the run, it
never blocks execution.

Configuration (all optional; without it the app runs exactly as before):

    NEAR_RPC_URL         default https://rpc.testnet.near.org
    NEAR_CONTRACT        e.g. policy.safecell-test-1.testnet
    NEAR_AGENT_ACCOUNT   e.g. agent.safecell-test-1.testnet
    NEAR_AGENT_KEY       ed25519:... (function-call key, `anchor` only)
    NEAR_COST_YOCTO      declared value at risk per execution, default 0.001 NEAR
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, cast

from .near_client import Account, NearError, Rpc, action_function_call, outcome_summary

DEFAULT_COST = 10**21  # 0.001 NEAR, in yoctoNEAR

_lock = threading.Lock()


class AnchorService:
    def __init__(self) -> None:
        self.rpc_url = os.environ.get("NEAR_RPC_URL", "https://rpc.testnet.near.org")
        self.contract = os.environ.get("NEAR_CONTRACT", "").strip()
        self.account_id = os.environ.get("NEAR_AGENT_ACCOUNT", "").strip()
        self.secret = os.environ.get("NEAR_AGENT_KEY", "").strip()
        self.cost = int(os.environ.get("NEAR_COST_YOCTO", DEFAULT_COST))
        self.rpc = Rpc(self.rpc_url)
        self._signer: Account | None = None

    @property
    def configured(self) -> bool:
        return bool(self.contract and self.account_id and self.secret)

    @property
    def signer(self) -> Account:
        if self._signer is None:
            self._signer = Account.from_secret(self.account_id, self.secret)
        return self._signer

    # ------------------------------------------------------------- read

    def chain(self) -> dict[str, Any]:
        """Current on-chain state. Raises NearError when the node is unreachable."""
        head = self.rpc.view(self.contract, "head")
        length = self.rpc.view(self.contract, "chain_len")
        remaining = self.rpc.view(self.contract, "remaining_today")
        policy = self.rpc.view(self.contract, "get_policy")
        return {
            "contract": self.contract,
            "head": head,
            "chain_len": length,
            "remaining_today": remaining,
            "policy": policy,
        }

    def lookup(self, execution_id: str) -> dict | None:
        return cast("dict | None",
                    self.rpc.view(self.contract, "get_anchor", {"execution_id": execution_id}))

    def verify_chain(self) -> bool | None:
        try:
            return bool(self.rpc.view(self.contract, "verify_chain"))
        except NearError:
            return None

    # ------------------------------------------------------------- write

    def anchor(self, execution_id: str, receipt_hash: str,
               attempts: int = 3) -> dict[str, Any]:
        """Write one anchor, re-reading the head if another writer got there first.

        The contract rejects a stale `prev_hash` with ERR_CHAIN_BREAK, so the
        retry is a re-read of on-chain state, not a guess.
        """
        if not self.configured:
            return {"status": "unconfigured"}
        with _lock:
            last: NearError | None = None
            for attempt in range(1, attempts + 1):
                prev = self.rpc.view(self.contract, "head")
                args = {
                    "execution_id": execution_id,
                    "receipt_hash": receipt_hash,
                    "prev_hash": prev,
                    "action": "exec:python3",
                    "cost": str(self.cost),
                }
                try:
                    result = self.rpc.send(
                        self.signer, self.contract,
                        [action_function_call("anchor", args)],
                    )
                    summary = outcome_summary(result)
                    return {
                        "status": "anchored" if summary["ok"] else "failed",
                        "tx_hash": summary["tx_hash"],
                        "gas_burnt": summary["gas_burnt"],
                        "seq": summary["value"],
                        "prev_hash": prev,
                        "receipt_hash": receipt_hash,
                        "attempts": attempt,
                        "ts_ms": int(time.time() * 1000),
                    }
                except NearError as e:
                    last = e
                    time.sleep(0.8 * attempt)
            return {"status": "failed", "error": str(last)}


SERVICE = AnchorService()
