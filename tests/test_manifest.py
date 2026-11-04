"""Manifest tests: the declaration must be derivable, stable and honest."""

from __future__ import annotations

import unittest

from safecell import manifest as m
from safecell.policy import PRESETS, Policy


class ManifestTests(unittest.TestCase):
    def test_same_policy_same_hash(self):
        a = m.build(PRESETS["strict"], "strict")
        b = m.build(PRESETS["strict"], "strict")
        self.assertEqual(m.manifest_hash(a), m.manifest_hash(b))

    def test_policy_change_changes_hash(self):
        tight = Policy(memory_mb=256)
        loose = Policy(memory_mb=1024)
        self.assertNotEqual(
            m.manifest_hash(m.build(tight, "x")),
            m.manifest_hash(m.build(loose, "x")),
        )

    def test_declared_limits_match_policy(self):
        p = PRESETS["paranoid"]
        man = m.build(p, "paranoid")["declared"]["compute"]
        self.assertEqual(man["memory_mb"], p.memory_mb)
        self.assertEqual(man["cpu_seconds"], p.cpu_seconds)
        self.assertEqual(man["wall_clock_s"], p.wall_timeout_s)
        self.assertEqual(man["max_processes"], p.max_processes)

    def test_network_is_declared_off(self):
        man = m.build(PRESETS["strict"], "strict")
        self.assertIn("none", man["declared"]["network"]["interfaces"])

    def test_host_fs_not_declared_readable(self):
        man = m.build(PRESETS["strict"], "strict")
        ro = man["declared"]["filesystem"]["read_only"]
        for forbidden in ("/home", "/root", "/mnt", "/media"):
            self.assertNotIn(forbidden, ro)

    def test_kernel_limit_is_disclosed(self):
        man = m.build(PRESETS["strict"], "strict")
        caps = [x["capability"] for x in man["not_denied"]]
        self.assertIn("kernel_exploit", caps,
                      "manifest must not imply kernel-level isolation")

    def test_markdown_renders_all_sections(self):
        man = m.build(PRESETS["strict"], "strict")
        md = m.to_markdown(man, m.manifest_hash(man))
        for heading in ("# Permission manifest", "## Granted", "## Denied",
                        "## Explicitly not denied"):
            self.assertIn(heading, md)

    def test_hash_shape(self):
        man = m.build(PRESETS["default"], "default")
        h = m.manifest_hash(man)
        self.assertTrue(h.startswith("sha256:"))
        self.assertEqual(len(h), len("sha256:") + 64)


if __name__ == "__main__":
    unittest.main(verbosity=2)
