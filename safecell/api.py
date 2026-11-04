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


@app.get("/api/runs")
def list_runs():
    runs = sorted(STORE.all(), key=lambda r: r.started_ms or 0, reverse=True)[:20]
    return [{"id": r.id, "task": r.task[:80], "status": r.status,
             "started_ms": r.started_ms} for r in runs]
