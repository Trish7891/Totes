"""Sandbox policy: the contract every execution is held to."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Policy:
    """Resource and isolation limits applied to every sandboxed execution.

    Frozen so a run's policy cannot be mutated mid-flight; the fingerprint of
    this object is recorded in every receipt and every event.
    """

    # Wall clock and CPU
    wall_timeout_s: float = 20.0
    cpu_seconds: int = 10

    # Memory / disk
    memory_mb: int = 512
    max_file_mb: int = 8
    workspace_mb: int = 32

    # Process table
    max_processes: int = 64
    max_open_files: int = 256

    # Output
    max_output_bytes: int = 64_000

    # Isolation switches
    network: bool = False          # False => --unshare-net, no egress at all
    allow_host_fs: bool = False    # never bind the host home/tmp into the cell
    env_allowlist: tuple[str, ...] = field(
        default=("PATH", "LANG", "LC_ALL", "TZ", "PYTHONUNBUFFERED", "HOME")
    )

    def fingerprint(self) -> str:
        blob = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()[:32]


DEFAULT = Policy()

# Named presets. `strict` is what the API uses; `demo` keeps a short timeout so
# the containment demo finishes quickly on stage.
PRESETS: dict[str, Policy] = {
    "default": DEFAULT,
    "strict": Policy(wall_timeout_s=15.0, cpu_seconds=8, memory_mb=384),
    "demo": Policy(wall_timeout_s=6.0, cpu_seconds=4, memory_mb=256),
    "paranoid": Policy(
        wall_timeout_s=10.0,
        cpu_seconds=5,
        memory_mb=256,
        max_output_bytes=16_000,
        max_processes=16,
        workspace_mb=8,
    ),
}


def get_preset(name: str | None) -> Policy:
    if not name:
        return PRESETS["strict"]
    return PRESETS.get(name, PRESETS["strict"])
