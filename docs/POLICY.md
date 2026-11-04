# Permission manifest — `strict`

`safecell.manifest/v1` · `sha256:61ff3f910936278609e16ad5c29f2812dacbd3c77bdd6747af6d6d6390593d19`

Generated from the same `Policy` object the sandbox enforces. Every run
carries this manifest and its hash in its receipt.

## Granted

**Filesystem**

- Read-only: `/usr`, `/lib`, `/lib64`, `/bin`, `/sbin`, `/etc`
- Writable: `/work`, `/tmp` (tmpfs, per run)
- Host paths: not present (never bind-mounted)

**Network**

- Interfaces: none (unshare-net)
- DNS: none

**Compute**

- wall clock s: 15.0
- cpu seconds: 8
- memory mb: 384
- max file mb: 8
- max output bytes: 64000
- max processes: 64
- max open files: 256

**Environment**

- Inherited from host: False
- Allowlist: PATH, LANG, LC_ALL, TZ, PYTHONUNBUFFERED, HOME

**Secrets**

- Inbound: none (host env never passed to the child)
- Outbound: output scrubbed for credential shapes before delivery

## Denied

- `read_host_files` — host trees are not bound; paths do not exist
- `write_host_files` — no writable mount outside the cell's tmpfs
- `network_egress` — network namespace with no interfaces
- `read_host_env` — --clearenv plus explicit allowlist
- `persist_state` — workspace is tmpfs, destroyed per call
- `fork_bomb` — RLIMIT_NPROC headroom above ambient task count
- `exhaust_cpu` — RLIMIT_CPU plus wall-clock process-group kill
- `exhaust_memory` — RLIMIT_AS

## Explicitly not denied

- `kernel_exploit` — namespaces share the host kernel; a kernel bug is a shared boundary. Mitigation is a microVM backend, not this layer.

Enforced by: bubblewrap namespaces + rlimits + wall-clock kill + output scrubbing.

