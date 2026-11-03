"""Tamper-evident run log.

Every run emits a JSON event chain: each event carries the hash of the previous
one, so the receipt at the end *commits* to the whole history. A receipt that
verifies means the log you are reading is the log that ran.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any


def _digest(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


@dataclass
class Event:
    seq: int
    kind: str
    data: dict[str, Any]
    ts_ms: int
    prev_hash: str
    hash: str = ""

    def compute(self) -> str:
        body = {
            "seq": self.seq,
            "kind": self.kind,
            "data": self.data,
            "ts_ms": self.ts_ms,
            "prev": self.prev_hash,
        }
        return _digest(body)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "kind": self.kind,
            "data": self.data,
            "ts_ms": self.ts_ms,
            "prev": self.prev_hash,
            "hash": self.hash,
        }


@dataclass
class EventLog:
    run_id: str
    events: list[Event] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def append(self, kind: str, data: dict[str, Any]) -> Event:
        with self._lock:
            prev = self.events[-1].hash if self.events else "genesis"
            ev = Event(
                seq=len(self.events) + 1,
                kind=kind,
                data=data,
                ts_ms=int(time.time() * 1000),
                prev_hash=prev,
            )
            ev.hash = ev.compute()
            self.events.append(ev)
            return ev

    @property
    def head(self) -> str:
        return self.events[-1].hash if self.events else "genesis"

    def verify(self) -> bool:
        prev = "genesis"
        for ev in self.events:
            if ev.prev_hash != prev or ev.compute() != ev.hash:
                return False
            prev = ev.hash
        return True

    def receipt(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {
            "run_id": self.run_id,
            "chain_head": self.head,
            "event_count": len(self.events),
            "verified": self.verify(),
            "first_ts_ms": self.events[0].ts_ms if self.events else None,
            "last_ts_ms": self.events[-1].ts_ms if self.events else None,
        }
        if extra:
            payload.update(extra)
        return payload
