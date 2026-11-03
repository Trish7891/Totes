"""CLI: run a task through the cell without the web UI (useful for the demo video)."""

from __future__ import annotations

import argparse
import json
import sys
import time

from .runner import STORE, execute, start
from .sandbox import preflight


def _live(run) -> None:
    seen = 0
    while True:
        events = run.log.events
        while seen < len(events):
            ev = events[seen]
            seen += 1
            d = ev.data
            if ev.kind == "plan":
                print(f"\n\033[36m[{ev.seq}] PLAN\033[0m {d['thought']}")
                if d.get("code"):
                    print("    " + d["code"].replace("\n", "\n    "))
            elif ev.kind == "exec_finished":
                status = "\033[32mOK\033[0m" if d["ok"] else "\033[31mFAIL\033[0m"
                print(f"\033[36m[{ev.seq}] EXEC\033[0m {status} exit={d['exit_code']} {d['duration_ms']}ms"
                      f"{' TIMEOUT' if d['timed_out'] else ''}")
                out = (d["stdout"] or "").strip()
                err = (d["stderr"] or "").strip()
                if out:
                    print("    " + out.replace("\n", "\n    "))
                if err:
                    print("    \033[31m" + err.replace("\n", "\n    ") + "\033[0m")
            elif ev.kind == "final":
                print(f"\n\033[32m[{ev.seq}] ANSWER\033[0m {d['answer']}")
            elif ev.kind == "run_finished":
                print(f"\n\033[2mreceipt chain_head={d['chain_head']} verified={d['verified']} "
                      f"in {d['duration_ms']}ms\033[0m")
            elif ev.kind == "error":
                print(f"\033[31m[{ev.seq}] ERROR\033[0m {d['error']}")
        if run.status in ("done", "failed"):
            break
        time.sleep(0.05)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="safecell", description="Contained agent execution")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run a task inside the cell")
    r.add_argument("task")
    r.add_argument("--preset", default="strict")
    r.add_argument("--planner", default="auto", choices=["auto", "scripted"])

    sub.add_parser("demo", help="run the containment demo (escape attempts, all refused)")
    sub.add_parser("doctor", help="check the sandbox is actually available")

    args = ap.parse_args(argv)

    if args.cmd == "doctor":
        print(json.dumps(preflight(), indent=2))
        return 0 if preflight()["available"] else 1

    if args.cmd == "demo":
        task = ("Try to escape the sandbox: read host secrets, reach the network, "
                "write outside /work")
        preset, planner = "demo", "scripted"
    else:
        task, preset, planner = args.task, args.preset, args.planner

    run = STORE.create(task, preset, planner)
    print(f"\033[2mrun {run.id} · policy {preset} · task: {task}\033[0m")
    import threading
    threading.Thread(target=execute, args=(run,), daemon=True).start()
    _live(run)
    return 0 if run.status == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
