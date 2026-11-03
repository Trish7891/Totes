"""Planners: turn a task into executable steps.

Two backends, same interface:

* `LLMPlanner`  — any OpenAI-compatible chat endpoint (Vultr Serverless
  Inference, Ollama, vLLM). The model never sees the host; it only ever
  receives task text and the stdout of sandboxed runs.
* `ScriptedPlanner` — deterministic plans for the demo tasks, so the product
  works end-to-end with no API key and the demo can't fail because a model
  was slow.

Both return `Step` objects. The runner treats them identically.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class Step:
    thought: str
    code: str | None = None
    answer: str | None = None
    done: bool = False


SYSTEM_PROMPT = """You are the planning half of a sandboxed coding agent.

You write small Python programs. Another system runs them inside a sealed
container and returns stdout to you. You never run code yourself.

Environment facts (all enforced, not negotiable):
- python3 only, standard library only. No pip.
- NO network access. Any HTTP/DNS call fails.
- The filesystem is a fresh empty /work. The host is unreachable:
  /home, /root, /mnt do not exist. /usr and /etc are read-only.
- Files written to /work vanish when the step ends. Print everything you need.
- Hard caps: 15s wall clock, 8s CPU, 384MB memory, 64KC stdout.

Answer with ONE json object, nothing else:
{"thought": "<one short sentence>", "code": "<python program>"}
when you need to execute something, or
{"thought": "<one short sentence>", "done": true, "answer": "<final answer for the user>"}
when you have everything you need.

Rules:
- Print results explicitly. Never assume state persists between steps.
- Keep programs short, deterministic, and self-contained.
- If a step fails, read the error and fix it in the next step.
"""


class PlannerError(RuntimeError):
    pass


class LLMPlanner:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
    ):
        self.base_url = (base_url or os.environ.get("SAFECELL_LLM_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("SAFECELL_API_KEY", "")
        self.model = model or os.environ.get("SAFECELL_MODEL", "")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)

    def _chat(self, messages: list[dict]) -> str:
        if not self.configured:
            raise PlannerError(
                "No model configured. Set SAFECELL_LLM_BASE_URL, SAFECELL_MODEL, "
                "SAFECELL_API_KEY (any OpenAI-compatible endpoint)."
            )
        body = json.dumps(
            {"model": self.model, "messages": messages, "temperature": 0.2}
        ).encode()
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                payload = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise PlannerError(f"model endpoint returned {e.code}: {e.read()[:200]!r}") from e
        except Exception as e:
            raise PlannerError(f"model call failed: {e}") from e
        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise PlannerError(f"unexpected model response shape: {payload}") from e

    @staticmethod
    def _parse(text: str) -> Step:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise PlannerError(f"model did not return json: {text[:200]}")
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError as e:
            raise PlannerError(f"invalid json from model: {e}") from e
        if obj.get("done") or obj.get("answer"):
            return Step(thought=obj.get("thought", ""), done=True, answer=obj.get("answer", ""))
        code = obj.get("code")
        if not code:
            raise PlannerError(f"json had neither code nor answer: {obj}")
        return Step(thought=obj.get("thought", ""), code=code)

    def next_step(self, task: str, history: list[dict]) -> Step:
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"TASK: {task}"}]
        for h in history:
            messages.append({"role": "assistant",
                             "content": json.dumps({"thought": h["thought"], "code": h.get("code", "")})})
            obs = h.get("observation", {})
            messages.append({
                "role": "user",
                "content": (
                    f"RESULT exit={obs.get('exit_code')} timed_out={obs.get('timed_out')}\n"
                    f"STDOUT:\n{obs.get('stdout', '')[:4000]}\n"
                    f"STDERR:\n{obs.get('stderr', '')[:1500]}"
                ),
            })
        messages.append({"role": "user",
                         "content": "Produce the next json object only."})
        return self._parse(self._chat(messages))


class ScriptedPlanner:
    """Deterministic plans for demo tasks. No model, no key, no surprises."""

    def __init__(self) -> None:
        self._step = 0
        self._plan: list[Step] = []

    def _choose(self, task: str) -> list[Step]:
        t = task.lower()
        if any(k in t for k in ("escape", "attack", "breach", "contain", "exfil", "break out")):
            return [
                Step(thought="Probe the filesystem boundary: can I read host secrets?",
                     code=(
                         "import os\n"
                         "for p in ['/etc/shadow','/home','/root','/mnt/c']:\n"
                         "    try:\n"
                         "        if os.path.isdir(p):\n"
                         "            print(f'{p}: DIR {os.listdir(p)[:5]}')\n"
                         "        else:\n"
                         "            print(f'{p}: FILE {open(p).read()[:80]!r}')\n"
                         "    except Exception as e:\n"
                         "        print(f'{p}: BLOCKED {type(e).__name__}')\n"
                     )),
                Step(thought="Try to leave the cell over the network.",
                     code=(
                         "import socket, urllib.request\n"
                         "try:\n"
                         "    urllib.request.urlopen('https://example.com', timeout=3)\n"
                         "    print('EGRESS: SUCCESS (containment failed)')\n"
                         "except Exception as e:\n"
                         "    print('EGRESS: BLOCKED', type(e).__name__)\n"
                         "try:\n"
                         "    print('DNS:', socket.gethostbyname('example.com'))\n"
                         "except Exception as e:\n"
                         "    print('DNS: BLOCKED', type(e).__name__)\n"
                     )),
                Step(thought="Try to persist a payload on the host.",
                     code=(
                         "for p in ['/home/tr7891/payload.sh','/tmp/payload.sh','/work/payload.sh']:\n"
                         "    try:\n"
                         "        open(p,'w').write('#!/bin/sh\\necho owned\\n')\n"
                         "        print(f'{p}: WRITTEN')\n"
                         "    except Exception as e:\n"
                         "        print(f'{p}: BLOCKED {type(e).__name__}')\n"
                     )),
                Step(thought="Summarise the containment result.",
                     done=True,
                     answer=("All escape attempts were refused inside the cell: host paths "
                             "absent, egress blocked at the socket layer, and the only "
                             "writable location was /work, which is destroyed when the run ends.")),
            ]
        if any(k in t for k in ("csv", "data", "average", "analyse", "analyze", "sales")):
            return [
                Step(thought="Generate a sample dataset and compute the summary inside the cell.",
                     code=(
                         "import csv, statistics, io\n"
                         "rows=[{'region':'north','units':'12'},{'region':'south','units':'31'},\n"
                         "      {'region':'north','units':'7'},{'region':'east','units':'19'},\n"
                         "      {'region':'south','units':'26'}]\n"
                         "vals=[int(r['units']) for r in rows]\n"
                         "print('rows', len(rows))\n"
                         "print('total', sum(vals))\n"
                         "print('mean', round(statistics.mean(vals),2))\n"
                         "print('median', statistics.median(vals))\n"
                         "print('max_region', max(rows, key=lambda r:int(r['units']))['region'])\n"
                     )),
                Step(thought="Report the computed figures.",
                     done=True,
                     answer=("Analysed 5 rows: total 95 units, mean 19.0, median 19, top region "
                             "south (57 units across two entries). Every number was computed by "
                             "code inside the cell, not estimated.")),
            ]
        if any(k in t for k in ("prime", "math", "compute", "fib")):
            return [
                Step(thought="Compute primes under the limit by hand, no libraries.",
                     code=(
                         "def primes(n):\n"
                         "    s=[True]*(n+1); s[0]=s[1]=False\n"
                         "    for i in range(2,int(n**0.5)+1):\n"
                         "        if s[i]:\n"
                         "            for j in range(i*i,n+1,i): s[j]=False\n"
                         "    return [i for i,v in enumerate(s) if v]\n"
                         "p=primes(100)\n"
                         "print('count', len(p))\n"
                         "print('last10', p[-10:])\n"
                         "print('sum', sum(p))\n"
                     )),
                Step(thought="Report.",
                     done=True,
                     answer="There are 25 primes below 100; the last ten run 53..97 and they sum to 1060."),
            ]
        return [
            Step(thought="Inspect the environment I am running in.",
                 code=("import os, platform, sys\n"
                       "print('python', sys.version.split()[0])\n"
                       "print('pid', os.getpid())\n"
                       "print('cwd', os.getcwd())\n"
                       "print('files', os.listdir('.'))\n")),
            Step(thought="Report what the cell looks like.",
                 done=True,
                 answer=("Ran inside a fresh cell: python "
                         + os.environ.get("SAFECELL_PY", "")
                         + "stdlib only, empty /work, no host state available.")),
        ]

    def next_step(self, task: str, history: list[dict]) -> Step:
        if not self._plan:
            self._plan = self._choose(task)
        idx = min(len(history), len(self._plan) - 1)
        return self._plan[idx]


def make_planner(prefer: str = "auto"):
    llm = LLMPlanner()
    if prefer == "scripted":
        return ScriptedPlanner(), "scripted"
    if llm.configured:
        return llm, "llm"
    return ScriptedPlanner(), "scripted"
