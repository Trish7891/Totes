"""Containment test suite.

Each test asserts one containment property *from inside the cell*. These are
the receipts the demo video shows: not "we intended to isolate it" but
"here is the escape attempt, here is it failing".
"""

from __future__ import annotations

import os
import pathlib
import unittest

from safecell.policy import PRESETS, Policy
from safecell.sandbox import exec_in_cell, preflight

POLICY = Policy(wall_timeout_s=8.0, cpu_seconds=5, memory_mb=512, max_output_bytes=4096)


def run(code: str, policy: Policy | None = None):
    return exec_in_cell(code, policy or POLICY)


@unittest.skipUnless(preflight()["available"], "bubblewrap not available")
class ContainmentTests(unittest.TestCase):
    def test_normal_work_runs(self):
        r = run("print(sum(range(10)))")
        self.assertTrue(r.ok, r.stderr)
        self.assertEqual(r.stdout.strip(), "45")
        self.assertEqual(r.exit_code, 0)

    def test_host_home_invisible(self):
        r = run(
            "import os\n"
            "print('EXISTS' if os.path.exists('/home') else 'ABSENT')\n"
        )
        self.assertIn("ABSENT", r.stdout)

    def test_cannot_write_to_host(self):
        target = pathlib.Path("/home/tr7891/PWNED_BY_AGENT.txt")
        target.unlink(missing_ok=True)
        r = run(
            "open('/home/tr7891/PWNED_BY_AGENT.txt','w').write('owned')\n"
        )
        self.assertFalse(r.ok)
        self.assertFalse(target.exists(), "escape wrote to host filesystem!")

    def test_root_is_read_only(self):
        r = run("open('/usr/bin/pwned','w').write('x')\n")
        self.assertFalse(r.ok)
        self.assertIn("Read-only", r.stderr + r.stdout)

    def test_network_egress_blocked(self):
        r = run(
            "import socket\n"
            "try:\n"
            "    socket.create_connection(('1.1.1.1', 80), timeout=3)\n"
            "    print('NETWORK_OPEN')\n"
            "except OSError as e:\n"
            "    print('NETWORK_BLOCKED', type(e).__name__)\n"
        )
        self.assertIn("NETWORK_BLOCKED", r.stdout)
        self.assertNotIn("NETWORK_OPEN", r.stdout)

    def test_dns_blocked(self):
        r = run(
            "import socket\n"
            "try:\n"
            "    print('RESOLVED', socket.gethostbyname('example.com'))\n"
            "except OSError as e:\n"
            "    print('DNS_BLOCKED', type(e).__name__)\n"
        )
        self.assertIn("DNS_BLOCKED", r.stdout)

    def test_host_secrets_not_in_env(self):
        os.environ["AWS_SECRET_ACCESS_KEY"] = "should-never-cross-the-boundary"
        try:
            r = run(
                "import os\n"
                "leaked=[k for k in os.environ if 'AWS' in k or 'TOKEN' in k or 'KEY' in k]\n"
                "print('LEAKED' if leaked else 'CLEAN', sorted(os.environ))\n"
            )
        finally:
            del os.environ["AWS_SECRET_ACCESS_KEY"]
        self.assertNotIn("should-never-cross-the-boundary", r.stdout)
        self.assertIn("CLEAN", r.stdout)

    def test_secret_scrubbed_from_output(self):
        r = run("print('token is sk-abcdefghijklmnopqrstuvwxyz012345')\n")
        self.assertIn("[REDACTED]", r.stdout)
        self.assertNotIn("sk-abcdefghijklmnop", r.stdout)
        self.assertGreaterEqual(r.redactions, 1)

    def test_cpu_burner_is_killed_by_cpu_limit(self):
        r = run("while True:\n    pass\n", Policy(wall_timeout_s=30.0, cpu_seconds=1))
        self.assertFalse(r.ok)
        self.assertLess(r.duration_ms, 15000, "cpu limit did not bite")
        self.assertTrue(
            r.killed_signal or r.timed_out or (r.exit_code or 0) > 128,
            f"process was not killed (exit={r.exit_code}, sig={r.killed_signal})",
        )

    def test_wall_clock_timeout_kills_sleeper(self):
        # A sleeper burns no CPU, so only the wall-clock cap can stop it.
        r = run("import time\ntime.sleep(60)\n", Policy(wall_timeout_s=1.5, cpu_seconds=30))
        self.assertTrue(r.timed_out)
        self.assertFalse(r.ok)
        self.assertLess(r.duration_ms, 8000)

    def test_memory_limit(self):
        r = run("x = bytearray(1024*1024*1024)\nprint('ALLOCATED')\n")
        self.assertNotIn("ALLOCATED", r.stdout)
        self.assertFalse(r.ok)

    def test_process_spawn_limit(self):
        r = run(
            "import subprocess\n"
            "n=0\n"
            "try:\n"
            "    for i in range(500):\n"
            "        subprocess.Popen(['/bin/sleep','30'])\n"
            "        n+=1\n"
            "    print('SPAWNED', n)\n"
            "except OSError as e:\n"
            "    print('SPAWN_BLOCKED', n, type(e).__name__)\n",
            Policy(max_processes=64, wall_timeout_s=10),
        )
        self.assertIn("SPAWN_BLOCKED", r.stdout)

    def test_output_truncated(self):
        r = run("print('A'*20000)\n", Policy(max_output_bytes=1000))
        self.assertTrue(r.truncated)
        self.assertLessEqual(len(r.stdout), 1000)

    def test_workspace_is_ephemeral(self):
        run("open('/work/state.txt','w').write('secret-state')\n")
        r = run("import os; print('STATE_EXISTS' if os.path.exists('/work/state.txt') else 'FRESH')\n")
        self.assertIn("FRESH", r.stdout)

    def test_policy_fingerprint_recorded(self):
        r = run("print(1)\n")
        self.assertTrue(r.policy_fingerprint.startswith("sha256:"))

    def test_pid_namespace_isolated(self):
        r = run(
            "import os\n"
            "pids=[p for p in os.listdir('/proc') if p.isdigit()]\n"
            "print('PID', os.getpid(), 'VISIBLE', len(pids))\n"
        )
        pid, visible = int(r.stdout.split()[1]), int(r.stdout.split()[3])
        self.assertLessEqual(pid, 2, "cell process should be namespace leader")
        self.assertLess(visible, 20, f"cell can see {visible} processes; expected a private namespace")


if __name__ == "__main__":
    unittest.main(verbosity=2)
