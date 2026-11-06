"""HTTP surface: a small, production-shaped API plus the single-page UI."""

from __future__ import annotations

import json
import queue
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import manifest as manifest_mod
from .policy import PRESETS
from .near_anchor import SERVICE as NEAR_ANCHOR
from .near_client import NearError
from .planner import LLMPlanner
from .runner import STORE, start
from .sandbox import preflight

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="SafeCell", version="1.0.0",
              description="Contained agent execution: plan, dispatch, verify.")


class RunRequest(BaseModel):
    task: str = Field(min_length=3, max_length=2000)
    preset: str = "strict"
    planner: str = "auto"


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    llm = LLMPlanner()
    return {
        "ok": True,
        "sandbox": preflight(),
        "planner": "llm" if llm.configured else "scripted",
        "llm_model": llm.model or None,
        "presets": sorted(PRESETS),
        "near": {"configured": NEAR_ANCHOR.configured, "contract": NEAR_ANCHOR.contract or None},
    }


def _manifest_for(preset: str) -> tuple[dict, str]:
    if preset not in PRESETS:
        raise HTTPException(404, f"unknown preset; choose from {sorted(PRESETS)}")
    man = manifest_mod.build(PRESETS[preset], preset)
    return man, manifest_mod.manifest_hash(man)


@app.get("/api/policy/{preset}")
def get_manifest(preset: str):
    man, h = _manifest_for(preset)
    return {"manifest_hash": h, "manifest": man}


@app.get("/api/policy/{preset}/manifest.md")
def get_manifest_md(preset: str):
    man, h = _manifest_for(preset)
    return PlainTextResponse(manifest_mod.to_markdown(man, h), media_type="text/markdown")


@app.post("/api/runs")
def create_run(req: RunRequest):
    if req.preset not in PRESETS:
        raise HTTPException(400, f"unknown preset; choose from {sorted(PRESETS)}")
    pe = preflight()
    if not pe["available"]:
        raise HTTPException(
            503,
            "bubblewrap is unavailable on this host; refusing to execute unsandboxed.",
        )
    run = start(req.task, req.preset, req.planner)
    return {"id": run.id, "status": run.status}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    run = STORE.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run.snapshot()


@app.get("/api/runs/{run_id}/events")
def stream_events(run_id: str):
    run = STORE.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")

    q: queue.Queue = queue.Queue(maxsize=1000)
    # Replay what already happened, then follow live.
    for ev in list(run.log.events):
        q.put_nowait(ev.to_dict())
    if run.status in ("done", "failed"):
        q.put_nowait(None)
    else:
        run.subscribers.append(q)

    def gen():
        try:
            while True:
                try:
                    item = q.get(timeout=20)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    yield "event: end\ndata: {}\n\n"
                    break
                yield f"data: {json.dumps(item)}\n\n"
        finally:
            if q in run.subscribers:
                run.subscribers.remove(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/runs/{run_id}/verify")
def verify_run(run_id: str):
    run = STORE.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    valid = run.log.verify()
    return JSONResponse(
        {"run_id": run_id, "chain_verified": valid, "chain_head": run.log.head,
         "event_count": len(run.log.events)},
        status_code=200 if valid else 409,
    )


@app.get("/api/near")
def near_state():
    if not NEAR_ANCHOR.configured:
        return {"configured": False,
                "hint": "set NEAR_CONTRACT, NEAR_AGENT_ACCOUNT and NEAR_AGENT_KEY"}
    try:
        state = NEAR_ANCHOR.chain()
        state["verify_chain"] = NEAR_ANCHOR.verify_chain()
        state["configured"] = True
        return state
    except NearError as e:
        return JSONResponse({"configured": True, "error": str(e)}, status_code=503)


@app.get("/api/runs/{run_id}/proof")
def prove_run(run_id: str):
    """Compare the local receipt against the on-chain anchor.

    `match` is only true when the local log still verifies *and* its head is the
    hash that was anchored. Editing an event breaks the first check; rewriting
    the whole log with fresh hashes breaks the second.
    """
    run = STORE.get(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    log = run.log
    local_verified = bool(log and log.verify())
    local_head = log.head if log else None
    proof = {
        "run_id": run_id,
        "local": {"verified": local_verified, "chain_head": local_head,
                  "event_count": len(log.events) if log else 0},
        "anchor": run.anchor,
        "onchain": None,
        "contract": None,
        "match": False,
    }
    if not NEAR_ANCHOR.configured:
        proof["note"] = "NEAR not configured on this instance; nothing to compare against"
        return proof
    # A receipt that the anchor says landed can still be a block behind on the
    # RPC's view, so give the read the same few seconds the write took.
    import time as _t
    record, chain, last_err = None, None, None
    for attempt in range(4):
        try:
            record = NEAR_ANCHOR.lookup(run_id)
            chain = NEAR_ANCHOR.chain()
            last_err = None
            if record or (run.anchor or {}).get("status") != "anchored":
                break
        except NearError as e:
            last_err = e
        _t.sleep(1.5)
    if chain is None:
        proof["error"] = str(last_err)
        return JSONResponse(proof, status_code=503)
    proof["onchain"] = record
    proof["contract"] = {"head": chain["head"], "chain_len": chain["chain_len"],
                         "verify_chain": NEAR_ANCHOR.verify_chain()}
    proof["match"] = bool(
        record and local_verified and record.get("receipt_hash") == local_head
    )
    return proof


@app.get("/api/runs")
def list_runs():
    runs = sorted(STORE.all(), key=lambda r: r.started_ms or 0, reverse=True)[:20]
    return [{"id": r.id, "task": r.task[:80], "status": r.status,
             "started_ms": r.started_ms} for r in runs]
