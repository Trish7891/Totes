# Containment evidence

Captured from a live run of `python3 -m unittest tests.test_containment -v` on
the machine this service was built on (Linux, bubblewrap 0.11.1). No test mocks
the sandbox: each one executes code inside a real cell and asserts the outcome.

```
    test_cannot_write_to_host (tests.test_containment.ContainmentTests.test_cannot_write_to_host) ... ok
    test_cpu_burner_is_killed_by_cpu_limit (tests.test_containment.ContainmentTests.test_cpu_burner_is_killed_by_cpu_limit) ... ok
    test_dns_blocked (tests.test_containment.ContainmentTests.test_dns_blocked) ... ok
    test_host_home_invisible (tests.test_containment.ContainmentTests.test_host_home_invisible) ... ok
    test_host_secrets_not_in_env (tests.test_containment.ContainmentTests.test_host_secrets_not_in_env) ... ok
    test_memory_limit (tests.test_containment.ContainmentTests.test_memory_limit) ... ok
    test_network_egress_blocked (tests.test_containment.ContainmentTests.test_network_egress_blocked) ... ok
    test_normal_work_runs (tests.test_containment.ContainmentTests.test_normal_work_runs) ... ok
    test_output_truncated (tests.test_containment.ContainmentTests.test_output_truncated) ... ok
    test_pid_namespace_isolated (tests.test_containment.ContainmentTests.test_pid_namespace_isolated) ... ok
    test_policy_fingerprint_recorded (tests.test_containment.ContainmentTests.test_policy_fingerprint_recorded) ... ok
    test_process_spawn_limit (tests.test_containment.ContainmentTests.test_process_spawn_limit) ... ok
    test_root_is_read_only (tests.test_containment.ContainmentTests.test_root_is_read_only) ... ok
    test_secret_scrubbed_from_output (tests.test_containment.ContainmentTests.test_secret_scrubbed_from_output) ... ok
    test_wall_clock_timeout_kills_sleeper (tests.test_containment.ContainmentTests.test_wall_clock_timeout_kills_sleeper) ... ok
    test_workspace_is_ephemeral (tests.test_containment.ContainmentTests.test_workspace_is_ephemeral) ... ok
    
    ----------------------------------------------------------------------
    Ran 16 tests in 8.727s
    
    OK
```

## What each assertion proves

- **test_normal_work_runs** — the cell is usable, not just locked: `sum(range(10))` returns 45 with exit code 0.
- **test_host_home_invisible** — `/home` does not exist inside the cell. The operator's filesystem is not reachable by path.
- **test_cannot_write_to_host** — an attempted write to `/home/tr7891/PWNED_BY_AGENT.txt` fails *and* the file is verified absent on the host afterwards.
- **test_root_is_read_only** — `/usr` is bind-mounted read-only; writing a binary there fails.
- **test_network_egress_blocked** — a TCP connect to `1.1.1.1:80` raises `OSError`; the string `NETWORK_OPEN` never appears.
- **test_dns_blocked** — name resolution fails too, so "just use a hostname" is not a workaround.
- **test_host_secrets_not_in_env** — a sentinel `AWS_SECRET_ACCESS_KEY` set on the host does not appear inside the cell; its value never crosses the boundary.
- **test_secret_scrubbed_from_output** — a key-shaped string printed by sandboxed code is replaced with `[REDACTED]` before it reaches the log.
- **test_cpu_burner_is_killed_by_cpu_limit** — `while True: pass` dies at the CPU cap (signal death, ~1s), long before the wall clock.
- **test_wall_clock_timeout_kills_sleeper** — a sleeping program burns no CPU, so only the wall-clock cap can stop it — and it does, in under 8s.
- **test_memory_limit** — allocating 1 GB under a 512 MB `RLIMIT_AS` never reaches `ALLOCATED`.
- **test_process_spawn_limit** — 500 held-open child processes hit `RLIMIT_NPROC` and raise `OSError`.
- **test_output_truncated** — 20 KB of stdout is capped at the policy limit and flagged `truncated`.
- **test_workspace_is_ephemeral** — a file written to `/work` in one run is gone in the next; state does not accumulate.
- **test_pid_namespace_isolated** — the cell owns its own PID namespace: its process is the namespace leader and it sees a handful of PIDs, not the host process table.
- **test_policy_fingerprint_recorded** — every result carries the `sha256:` fingerprint of the policy it ran under, so a receipt can be tied to exact limits.

## Not claimed

- Kernel-level isolation is shared. A kernel exploit escapes the cell; a microVM backend would close that gap (see README *Limitations*).
- These tests do not prove that *any* code is safe, only that the boundary holds for these classes of escape attempt.
