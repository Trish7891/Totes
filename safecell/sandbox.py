"""The sandbox itself: bubblewrap + rlimits + timeout + output scrubbing.

Everything an agent's code touches goes through `exec_in_cell`. There is no
"trusted" path that skips it.
"""

from __future__ import annotations

import os
import re
import resource
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field

from .policy import Policy

BWRAP = shutil.which("bwrap")

# Read-only system trees. Deliberately absent: /home, /root, /mnt, /media,
# /var/run, and the host TMPDIR. Nothing from the operator's machine is
# reachable from inside the cell.
RO_BINDS = ["/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc"]

# Patterns we never want to see leave the cell, even if something inside
# manufactures one.
SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
]


@dataclass
class ExecResult:
    ok: bool
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    truncated: bool = False
    redactions: int = 0
    killed_signal: int | None = None
    policy_fingerprint: str = ""
    sandbox_engine: str = "bubblewrap"

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "truncated": self.truncated,
            "redactions": self.redactions,
            "killed_signal": self.killed_signal,
            "policy_fingerprint": self.policy_fingerprint,
            "sandbox_engine": self.sandbox_engine,
        }


def scrub(text: str) -> tuple[str, int]:
    """Replace anything that looks like a credential. Returns (text, count)."""
    hits = 0
    for pat in SECRET_PATTERNS:
        text, n = pat.subn("[REDACTED]", text)
        hits += n
    return text, hits


def _current_task_count() -> int:
    """Tasks (processes + threads) owned by our uid right now.

    RLIMIT_NPROC counts every task of the user, not just the cell's, so a limit
    below the ambient count makes clone() fail before the sandbox even starts.
    We therefore express the limit as *headroom above the ambient count*.
    """
    uid = os.getuid()
    total = 0
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/status") as f:
                    status = f.read()
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
            for line in status.splitlines():
                if line.startswith("Uid:"):
                    if int(line.split()[1]) != uid:
                        break
                elif line.startswith("Threads:"):
                    total += int(line.split()[1])
                    break
    except Exception:
        return 0
    return total


AMBIENT_TASKS = _current_task_count()


def _limits(policy: Policy):
    """Rlimits applied in the child before exec. Inherited by every process."""

    def apply() -> None:
        mb = 1024 * 1024
        nproc = AMBIENT_TASKS + policy.max_processes
        resource.setrlimit(resource.RLIMIT_CPU, (policy.cpu_seconds, policy.cpu_seconds))
        resource.setrlimit(resource.RLIMIT_AS, (policy.memory_mb * mb, policy.memory_mb * mb))
        resource.setrlimit(resource.RLIMIT_FSIZE, (policy.max_file_mb * mb, policy.max_file_mb * mb))
        resource.setrlimit(resource.RLIMIT_NPROC, (nproc, nproc))
        resource.setrlimit(resource.RLIMIT_NOFILE, (policy.max_open_files, policy.max_open_files))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return apply


def build_argv(policy: Policy, workdir: str, code_path: str) -> list[str]:
    if BWRAP is None:
        raise RuntimeError("bubblewrap not found; refusing to run unsandboxed")
    argv = [BWRAP, "--die-with-parent", "--new-session", "--unshare-pid", "--unshare-ipc",
            "--unshare-uts", "--unshare-cgroup-try"]
    if not policy.network:
        argv += ["--unshare-net"]
    for path in RO_BINDS:
        if os.path.exists(path):
            argv += ["--ro-bind", path, path]
    argv += ["--dev", "/dev", "--proc", "/proc"]
    argv += ["--tmpfs", "/tmp", "--tmpfs", "/work"]
    argv += ["--bind", workdir, "/out"]
    argv += ["--chdir", "/work"]
    argv += ["--clearenv"]
    for key in policy.env_allowlist:
        if key == "HOME":
            argv += ["--setenv", "HOME", "/work"]
        elif key in os.environ:
            argv += ["--setenv", key, os.environ[key]]
    argv += ["--setenv", "PYTHONDONTWRITEBYTECODE", "1"]
    argv += ["--", "python3", "/out/entry.py"]
    return argv


def _read_capped(path: str, cap: int) -> tuple[str, bool]:
    try:
        with open(path, "rb") as f:
            data = f.read(cap + 1)
    except FileNotFoundError:
        return "", False
    truncated = len(data) > cap
    return data[:cap].decode("utf-8", "replace"), truncated


def exec_in_cell(code: str, policy: Policy | None = None) -> ExecResult:
    """Run `code` inside a fresh disposable cell. Nothing survives the call."""
    policy = policy or Policy()
    started = time.monotonic()

    with tempfile.TemporaryDirectory(prefix="safecell-") as tmp:
        workdir = os.path.join(tmp, "out")
        os.makedirs(workdir, exist_ok=True)
        entry = os.path.join(workdir, "entry.py")
        with open(entry, "w") as f:
            f.write(code)

        out_path = os.path.join(tmp, "stdout")
        err_path = os.path.join(tmp, "stderr")
        argv = build_argv(policy, workdir, entry)

        timed_out = False
        proc = None
        with open(out_path, "wb") as out, open(err_path, "wb") as err:
            try:
                proc = subprocess.Popen(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    start_new_session=True,
                    preexec_fn=_limits(policy),
                    env={},  # host env never reaches the child
                )
                proc.wait(timeout=policy.wall_timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=5)
            except Exception as exc:  # pragma: no cover - defensive
                return ExecResult(
                    ok=False, exit_code=None, stdout="", stderr=f"sandbox failure: {exc}",
                    duration_ms=int((time.monotonic() - started) * 1000),
                    policy_fingerprint=policy.fingerprint(),
                )

        stdout, trunc_o = _read_capped(out_path, policy.max_output_bytes)
        stderr, trunc_e = _read_capped(err_path, policy.max_output_bytes)
        stdout, hits_o = scrub(stdout)
        stderr, hits_e = scrub(stderr)
        duration_ms = int((time.monotonic() - started) * 1000)

        exit_code = None if timed_out else proc.returncode
        ok = (not timed_out) and exit_code == 0
        # bwrap reports a signal death as 128+N (e.g. 137 = SIGKILL from the
        # CPU rlimit's hard cap); surface it as a real signal number.
        killed = None
        if exit_code is not None and exit_code > 128:
            killed = exit_code - 128
        return ExecResult(
            ok=ok,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            timed_out=timed_out,
            truncated=trunc_o or trunc_e,
            redactions=hits_o + hits_e,
            killed_signal=killed,
            policy_fingerprint=policy.fingerprint(),
        )


def preflight() -> dict:
    """Report whether real isolation is available; the UI shows this honestly."""
    return {
        "bubblewrap": BWRAP,
        "available": BWRAP is not None,
        "network_isolation": "unshare-net" if BWRAP else None,
        "note": "If bubblewrap is missing the service refuses to execute rather than failing open.",
    }
