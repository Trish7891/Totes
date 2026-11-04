"""Permission manifest: what a cell is allowed to do, declared up front.

The manifest is the human- and machine-readable statement of a policy. It is
emitted with every run and its hash is included in the receipt, so a run can be
tied to an exact statement of capabilities rather than to a vibe.

The point is that the declaration and the enforcement are generated from the
same `Policy` object: there is no second place to keep them in sync, and no way
to declare one thing while enforcing another.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from .policy import Policy
from .sandbox import RO_BINDS

SCHEMA = "safecell.manifest/v1"


def build(policy: Policy, name: str) -> dict:
    """Return the manifest for `policy`. Pure: same policy => same manifest."""
    return {
        "schema": SCHEMA,
        "policy": name,
        "declared": {
            "filesystem": {
                "read_only": list(RO_BINDS),
                "writable": ["/work", "/tmp"],
                "host_paths": "not present (never bind-mounted)",
            },
            "network": {
                "interfaces": "none (unshare-net)",
                "dns": "none",
            },
            "compute": {
                "wall_clock_s": policy.wall_timeout_s,
                "cpu_seconds": policy.cpu_seconds,
                "memory_mb": policy.memory_mb,
                "max_file_mb": policy.max_file_mb,
                "max_output_bytes": policy.max_output_bytes,
                "max_processes": policy.max_processes,
                "max_open_files": policy.max_open_files,
            },
            "environment": {
                "inherited_from_host": False,
                "allowlist": list(policy.env_allowlist),
            },
            "secrets": {
                "inbound": "none (host env never passed to the child)",
                "outbound": "output scrubbed for credential shapes before delivery",
            },
        },
        "denied": [
            {"capability": "read_host_files", "why": "host trees are not bound; paths do not exist"},
            {"capability": "write_host_files", "why": "no writable mount outside the cell's tmpfs"},
            {"capability": "network_egress", "why": "network namespace with no interfaces"},
            {"capability": "read_host_env", "why": "--clearenv plus explicit allowlist"},
            {"capability": "persist_state", "why": "workspace is tmpfs, destroyed per call"},
            {"capability": "fork_bomb", "why": "RLIMIT_NPROC headroom above ambient task count"},
            {"capability": "exhaust_cpu", "why": "RLIMIT_CPU plus wall-clock process-group kill"},
            {"capability": "exhaust_memory", "why": "RLIMIT_AS"},
        ],
        "not_denied": [
            {
                "capability": "kernel_exploit",
                "why": "namespaces share the host kernel; a kernel bug is a shared boundary. "
                       "Mitigation is a microVM backend, not this layer.",
            }
        ],
        "enforced_by": "bubblewrap namespaces + rlimits + wall-clock kill + output scrubbing",
    }


def manifest_hash(manifest: dict) -> str:
    blob = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()


def policy_dict(policy: Policy) -> dict:
    return asdict(policy)


def to_markdown(manifest: dict, manifest_hash_value: str) -> str:
    d = manifest["declared"]
    lines = [
        f"# Permission manifest — `{manifest['policy']}`",
        "",
        f"`{manifest['schema']}` · `{manifest_hash_value}`",
        "",
        "Generated from the same `Policy` object the sandbox enforces. Every run",
        "carries this manifest and its hash in its receipt.",
        "",
        "## Granted",
        "",
        "**Filesystem**",
        "",
        f"- Read-only: {', '.join(f'`{p}`' for p in d['filesystem']['read_only'])}",
        f"- Writable: {', '.join(f'`{p}`' for p in d['filesystem']['writable'])} (tmpfs, per run)",
        f"- Host paths: {d['filesystem']['host_paths']}",
        "",
        "**Network**",
        "",
        f"- Interfaces: {d['network']['interfaces']}",
        f"- DNS: {d['network']['dns']}",
        "",
        "**Compute**",
        "",
    ]
    for k, v in d["compute"].items():
        lines.append(f"- {k.replace('_', ' ')}: {v}")
    lines += [
        "",
        "**Environment**",
        "",
        f"- Inherited from host: {d['environment']['inherited_from_host']}",
        f"- Allowlist: {', '.join(d['environment']['allowlist'])}",
        "",
        "**Secrets**",
        "",
        f"- Inbound: {d['secrets']['inbound']}",
        f"- Outbound: {d['secrets']['outbound']}",
        "",
        "## Denied",
        "",
    ]
    for item in manifest["denied"]:
        lines.append(f"- `{item['capability']}` — {item['why']}")
    lines += ["", "## Explicitly not denied", ""]
    for item in manifest["not_denied"]:
        lines.append(f"- `{item['capability']}` — {item['why']}")
    lines += ["", f"Enforced by: {manifest['enforced_by']}.", ""]
    return "\n".join(lines)
