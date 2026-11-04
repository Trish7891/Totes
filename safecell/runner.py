"""The orchestration loop: plan -> dispatch into a cell -> observe -> repeat.

The runner is deliberately thin. Every side effect a model proposes goes
through `exec_in_cell`; there is no in-process execution path.
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from . import manifest as manifest_mod
from .events import EventLog
from .planner import PlannerError, make_planner
from .policy import get_preset
from .sandbox import exec_in_cell, preflight

MAX_STEPS = 6


@dataclass
class Run:
    id: str
    task: str
    preset: str
    planner_kind: str = "auto"
    status: str = "queued"          # queued | running | done | failed
    answer: str | None = None
    error: str | None = None
    steps: list[dict] = field(default_factory=list)
    log: EventLog | None = None
    subscribers: list[queue.Queue] = field(default_factory=list)
    started_ms: int | None = None
    finished_ms: int | None = None

    def __post_init__(self):
        self.log = EventLog(run_id=self.id)

    def emit(self, kind: str, data: dict[str, Any]) -> None:
        ev = self.log.append(kind, data)
        payload = ev.to_dict()
        for q in list(self.subscribers):
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass

    def close_streams(self) -> None:
        for q in list(self.subscribers):
            try:
                q.put_nowait(None)
            except queue.Full:
                pass

    def snapshot(self) -> dict[str, Any]:
        policy = get_preset(self.preset)
        return {
            "id": self.id,
            "task": self.task,
            "preset": self.preset,
            "status": self.status,
            "answer": self.answer,
            "error": self.error,
            "steps": self.steps,
            "receipt": self.log.receipt({
                "policy_fingerprint": policy.fingerprint(),
                "manifest_hash": manifest_mod.manifest_hash(
                    manifest_mod.build(policy, self.preset)),
                "policy": {
                    "wall_timeout_s": policy.wall_timeout_s,
                    "cpu_seconds": policy.cpu_seconds,
                    "memory_mb": policy.memory_mb,
                    "network": policy.network,
                },
                "duration_ms": (self.finished_ms - self.started_ms)
                if self.started_ms and self.finished_ms else None,
                "sandbox": preflight(),
            }),
        }


class RunStore:
    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    def create(self, task: str, preset: str = "strict", planner_kind: str = "auto") -> Run:
        run = Run(id=uuid.uuid4().hex[:12], task=task, preset=preset, planner_kind=planner_kind)
        with self._lock:
            self._runs[run.id] = run
        return run

    def get(self, run_id: str) -> Run | None:
        with self._lock:
            return self._runs.get(run_id)

    def all(self) -> list[Run]:
        with self._lock:
            return list(self._runs.values())


STORE = RunStore()


def execute(run: Run) -> None:
    """Run the agent loop to completion. Safe to call on a worker thread."""
    policy = get_preset(run.preset)
    man = manifest_mod.build(policy, run.preset)
    man_hash = manifest_mod.manifest_hash(man)
    run.status = "running"
    run.started_ms = int(time.time() * 1000)
    run.emit("run_started", {
        "task": run.task,
        "preset": run.preset,
        "policy_fingerprint": policy.fingerprint(),
        "policy": {
            "wall_timeout_s": policy.wall_timeout_s,
            "cpu_seconds": policy.cpu_seconds,
            "memory_mb": policy.memory_mb,
            "max_output_bytes": policy.max_output_bytes,
            "network": policy.network,
        },
        "sandbox": preflight(),
    })

    run.emit("policy_manifest", {
        "manifest_hash": man_hash,
        "manifest": man,
    })

    planner, kind = make_planner(run.planner_kind)
    run.emit("planner_selected", {"backend": kind})

    history: list[dict] = []
    try:
        for step_no in range(1, MAX_STEPS + 1):
            step = planner.next_step(run.task, history)
            run.emit("plan", {
                "step": step_no,
                "thought": step.thought,
                "kind": "final" if step.done else "code",
                "code": step.code,
            })

            if step.done:
                run.answer = step.answer
                run.emit("final", {"answer": step.answer, "steps_used": step_no})
                break

            run.emit("exec_started", {"step": step_no})
            result = exec_in_cell(step.code or "", policy)
            obs = result.to_dict()
            run.emit("exec_finished", {"step": step_no, **obs})

            history.append({"thought": step.thought, "code": step.code, "observation": obs})
            run.steps.append({"step": step_no, "thought": step.thought, "code": step.code, "observation": obs})
        else:
            run.answer = "Step budget exhausted without a final answer."
            run.emit("final", {"answer": run.answer, "steps_used": MAX_STEPS})

        run.status = "done"
    except PlannerError as e:
        run.status = "failed"
        run.error = str(e)
        run.emit("error", {"error": str(e)})
    except Exception as e:  # pragma: no cover - defensive
        run.status = "failed"
        run.error = f"internal error: {e}"
        run.emit("error", {"error": run.error})
    finally:
        run.finished_ms = int(time.time() * 1000)
        run.emit("run_finished", {
            "status": run.status,
            "duration_ms": run.finished_ms - (run.started_ms or run.finished_ms),
            "chain_head": run.log.head,
            "verified": run.log.verify(),
        })
        run.close_streams()


def start(task: str, preset: str = "strict", planner_kind: str = "auto") -> Run:
    run = STORE.create(task, preset, planner_kind)
    threading.Thread(target=execute, args=(run,), daemon=True).start()
    return run
