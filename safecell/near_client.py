"""A small NEAR JSON-RPC client: borsh transaction encoding + ed25519 signing.

SafeCell needs exactly two on-chain actions — deploy a contract and call one
method on it — so pulling in near-cli (a Node toolchain) or a heavy SDK would
be more surface area than the job needs. This module speaks the wire format
directly:

    borsh(Transaction) -> sha256 -> ed25519 sign -> base64 -> broadcast_tx_commit

`Action` covers CreateAccount, DeployContract, FunctionCall and AddKey, which is
the whole vocabulary used by `scripts/deploy_contract.py`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

import nacl.signing

DEFAULT_RPC = os.environ.get("NEAR_RPC_URL", "https://rpc.testnet.near.org")
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class NearError(RuntimeError):
    pass


# ---------------------------------------------------------------- base58


def b58encode(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    pad = len(raw) - len(raw.lstrip(b"\x00"))
    return "1" * pad + (out or "")


def b58decode(text: str) -> bytes:
    n = 0
    for ch in text:
        n = n * 58 + B58.index(ch)
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    pad = len(text) - len(text.lstrip("1"))
    return b"\x00" * pad + body


# ---------------------------------------------------------------- borsh


def b_str(value: str) -> bytes:
    raw = value.encode()
    return struct.pack("<I", len(raw)) + raw


def b_bytes(value: bytes) -> bytes:
    return struct.pack("<I", len(value)) + value


def b_vec(items: list[bytes]) -> bytes:
    return struct.pack("<I", len(items)) + b"".join(items)


def b_u64(value: int) -> bytes:
    return struct.pack("<Q", value)


def b_u128(value: int) -> bytes:
    return struct.pack("<QQ", value & 0xFFFFFFFFFFFFFFFF, value >> 64)


def b_option(value: bytes | None) -> bytes:
    return b"\x00" if value is None else b"\x01" + value


@dataclass(frozen=True)
class PublicKey:
    raw: bytes  # 32-byte ed25519

    @classmethod
    def from_b58(cls, text: str) -> "PublicKey":
        body = text.split(":", 1)[1] if ":" in text else text
        raw = b58decode(body)
        if len(raw) != 32:
            raise NearError(f"expected 32-byte ed25519 key, got {len(raw)}")
        return cls(raw)

    def borsh(self) -> bytes:
        return b"\x00" + self.raw

    def str(self) -> str:
        return "ed25519:" + b58encode(self.raw)


@dataclass(frozen=True)
class AccessKey:
    nonce: int = 0
    # (receiver_id, method_names, allowance) for a function-call key; None = full access
    function_call: tuple[str, list[str], int | None] | None = None

    def borsh(self) -> bytes:
        body = b_u64(self.nonce)
        if self.function_call is None:
            return body + b"\x01"
        receiver, methods, allowance = self.function_call
        perm = b"\x00"
        perm += b_option(b_u128(allowance) if allowance is not None else None)
        perm += b_str(receiver)
        perm += b_vec([b_str(m) for m in methods])
        return body + perm


# ---------------------------------------------------------------- actions


def action_create_account() -> bytes:
    return b"\x00"


def action_deploy(code: bytes) -> bytes:
    return b"\x01" + b_bytes(code)


def action_function_call(method: str, args: dict, gas: int = 100_000_000_000_000,
                         deposit: int = 0) -> bytes:
    payload = json.dumps(args, separators=(",", ":")).encode()
    return (
        b"\x02"
        + b_str(method)
        + b_bytes(payload)
        + b_u64(gas)
        + b_u128(deposit)
    )


def action_transfer(deposit: int) -> bytes:
    return b"\x03" + b_u128(deposit)


def action_add_key(pk: PublicKey, key: AccessKey) -> bytes:
    return b"\x05" + pk.borsh() + key.borsh()


# ---------------------------------------------------------------- tx


@dataclass
class Account:
    """A local signer. The secret never leaves this object."""

    account_id: str
    public_key: PublicKey
    signing_key: nacl.signing.SigningKey

    @classmethod
    def from_secret(cls, account_id: str, secret: str) -> "Account":
        body = secret.split(":", 1)[1] if secret.startswith("ed25519:") else secret
        sk = nacl.signing.SigningKey(b58decode(body))
        return cls(account_id, PublicKey(bytes(sk.verify_key)), sk)


def transaction_borsh(signer: Account, receiver: str, nonce: int,
                      block_hash: bytes, actions: list[bytes]) -> bytes:
    if len(block_hash) != 32:
        raise NearError("block hash must be 32 bytes")
    return (
        b_str(signer.account_id)
        + signer.public_key.borsh()
        + b_u64(nonce)
        + b_str(receiver)
        + block_hash
        + b_vec(actions)
    )


def signed_tx_borsh(signer: Account, receiver: str, nonce: int,
                    block_hash: bytes, actions: list[bytes]) -> bytes:
    tx = transaction_borsh(signer, receiver, nonce, block_hash, actions)
    signature = signer.signing_key.sign(hashlib.sha256(tx).digest()).signature
    return tx + b"\x00" + signature


# ---------------------------------------------------------------- rpc


class Rpc:
    def __init__(self, url: str = DEFAULT_RPC, timeout: float = 25.0):
        self.url = url
        self.timeout = timeout

    def call(self, method: str, params: dict | list) -> dict:
        body = json.dumps({"jsonrpc": "2.0", "id": "safecell", "method": method,
                           "params": params}).encode()
        req = urllib.request.Request(
            self.url, data=body,
            headers={"Content-Type": "application/json", "User-Agent": "safecell/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:400]
            raise NearError(f"{method}: HTTP {e.code}: {detail}") from None
        except urllib.error.URLError as e:
            raise NearError(f"{method}: {e.reason}") from None
        if "error" in payload:
            err = payload["error"]
            msg = err.get("cause", {}).get("name") or err.get("message", "rpc error")
            data = err.get("data") or err.get("cause", {}).get("info", {})
            raise NearError(f"{method}: {msg}: {json.dumps(data)[:300]}")
        return payload["result"]

    def block_hash(self) -> bytes:
        res = self.call("block", {"finality": "final"})
        return b58decode(res["header"]["hash"])

    def access_key(self, account_id: str, public_key: PublicKey) -> dict:
        res = self.call("query", {
            "request_type": "view_access_key",
            "finality": "final",
            "account_id": account_id,
            "public_key": public_key.str(),
        })
        # The node reports a missing key inside `result`, not as a JSON-RPC error,
        # so check for the payload rather than trusting the absence of "error".
        if "nonce" not in res:
            raise NearError(f"access key {public_key.str()} not found on {account_id}")
        return res

    def view(self, contract: str, method: str, args: dict | None = None) -> object:
        res = self.call("query", {
            "request_type": "call_function",
            "finality": "final",
            "account_id": contract,
            "method_name": method,
            "args_base64": base64.b64encode(
                json.dumps(args or {}, separators=(",", ":")).encode()).decode(),
        })
        if "error" in res:
            raise NearError(f"{contract}.{method}: {res.get('error')}")
        raw = bytes(res.get("result") or b"")
        if not raw:
            return None
        return json.loads(raw.decode())

    def send(self, signer: Account, receiver: str, actions: list[bytes],
             wait: bool = True) -> dict:
        nonce = self.access_key(signer.account_id, signer.public_key)["nonce"] + 1
        tx = signed_tx_borsh(signer, receiver, nonce, self.block_hash(), actions)
        tx_hash = b58encode(hashlib.sha256(tx).digest())
        encoded = base64.b64encode(tx).decode()
        try:
            if wait:
                return self.call("broadcast_tx_commit", [encoded])
            return self.call("broadcast_tx_async", [encoded])
        except (TimeoutError, OSError, urllib.error.URLError) as e:
            # The node may have executed the tx even though the reply never
            # arrived. The hash is local, so the caller can check on-chain state.
            raise NearError(f"broadcast {tx_hash}: {type(e).__name__}: {e}") from e


def outcome_summary(result: dict) -> dict:
    """Flatten a FinalExecutionOutcome into {ok, tx_hash, gas_burnt, logs, value}."""
    status = result.get("status", {})
    ok = "SuccessValue" in status or "SuccessReceiptId" in status
    logs: list[str] = []
    gas = 0
    for receipt in result.get("receipts_outcome", []):
        gas += int(receipt.get("outcome", {}).get("gas_burnt", 0))
        logs.extend(receipt.get("outcome", {}).get("logs", []))
    value = None
    if "SuccessValue" in status:
        raw = base64.b64decode(status["SuccessValue"] or "")
        try:
            value = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            value = raw.decode(errors="replace")
    return {
        "ok": ok,
        "tx_hash": result.get("transaction_outcome", {}).get("id"),
        "block_hash": result.get("transaction_outcome", {}).get("block_hash"),
        "gas_burnt": gas,
        "logs": logs,
        "value": value,
        "status": status,
    }


def wait_for_tx(rpc: Rpc, tx_hash: str, attempts: int = 20, delay: float = 1.0) -> dict:
    """Poll `tx` until the transaction is final or attempts run out."""
    last: NearError | None = None
    for _ in range(attempts):
        try:
            res = rpc.call("tx", {"tx_hash": tx_hash, "sender_account_id": "",
                                  "wait_until": "FINAL"})
            return outcome_summary(res)
        except NearError as e:  # not yet indexed
            last = e
            time.sleep(delay)
    raise NearError(f"tx {tx_hash} not final: {last}")
