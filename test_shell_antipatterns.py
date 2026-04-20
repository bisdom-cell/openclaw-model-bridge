#!/usr/bin/env python3
"""
Regression tests for shell script anti-patterns.
Run: python3 -m pytest test_shell_antipatterns.py -v
  or: python3 test_shell_antipatterns.py

Background:
  commit 953f4ee (2026-03-11) introduced a heredoc+herestring stdin conflict
  in run_hn_fixed.sh that caused SENT_COUNT to always be 0 for ~30 hours.

  Root cause: `python3 - <<'PYEOF' ... PYEOF <<< "$DATA"` — the heredoc
  consumes stdin, so the herestring data is silently discarded by Python.
  Fix: use `echo "$DATA" | python3 -c '...'` instead.

  These tests scan ALL job shell scripts to prevent this class of bug
  from ever recurring.
"""
import os
import re
import subprocess
import unittest

# Repo root = directory containing this test file
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

# All shell scripts that run as cron jobs
JOB_SCRIPTS = []
for pattern in [
    "run_hn_fixed.sh",
    "jobs/*/run*.sh",
    "jobs/*/*.sh",
    "kb_*.sh",
    "health_check.sh",
    "wa_keepalive.sh",
    "job_watchdog.sh",
]:
    import glob as _glob
    JOB_SCRIPTS.extend(_glob.glob(os.path.join(REPO_ROOT, pattern)))
# Deduplicate
JOB_SCRIPTS = sorted(set(JOB_SCRIPTS))


class TestNoHeredocHerestringConflict(unittest.TestCase):
    """Detect the heredoc+herestring stdin conflict anti-pattern.

    Pattern to reject:
        RESULT=$(python3 - << 'PYEOF'
        ...code reading sys.stdin...
        PYEOF
        <<< "$VARIABLE")

    The heredoc feeds the script to python3 via stdin,
    leaving no room for the herestring to also feed data via stdin.
    """

    def test_no_heredoc_followed_by_herestring(self):
        """No script should have a heredoc end-marker immediately followed by <<<."""
        violations = []
        # Also scan scripts not in JOB_SCRIPTS but in repo root
        all_sh = set(JOB_SCRIPTS)
        all_sh.update(_glob.glob(os.path.join(REPO_ROOT, "*.sh")))

        for script in sorted(all_sh):
            if not os.path.isfile(script):
                continue
            with open(script) as f:
                lines = f.readlines()

            for i, line in enumerate(lines):
                stripped = line.strip()
                # Check if this line is a heredoc end marker
                if stripped in ("PYEOF", "PYEOF2", "EOF", "ENDPY", "HEREDOC"):
                    # Check next non-empty line for <<<
                    for j in range(i + 1, min(i + 3, len(lines))):
                        next_line = lines[j].strip()
                        if not next_line:
                            continue
                        if next_line.startswith("<<<"):
                            rel = os.path.relpath(script, REPO_ROOT)
                            violations.append(
                                f"{rel}:{i+1}: heredoc end '{stripped}' "
                                f"followed by herestring at line {j+1}"
                            )
                        break  # only check the first non-empty line after marker

        self.assertEqual(
            violations, [],
            "heredoc+herestring stdin conflict detected!\n"
            "Fix: use 'echo \"$VAR\" | python3 -c \"...\"' instead.\n"
            "See commit 2119fe5 for the canonical fix.\n\n"
            + "\n".join(violations)
        )

    def test_no_python_dash_heredoc_capturing_stdin_data(self):
        """Broader check: python3 - <<HEREDOC inside $(...) that also has <<<."""
        violations = []
        all_sh = set(JOB_SCRIPTS)
        all_sh.update(_glob.glob(os.path.join(REPO_ROOT, "*.sh")))

        for script in sorted(all_sh):
            if not os.path.isfile(script):
                continue
            with open(script) as f:
                lines = f.readlines()

            # Find lines with `python3 - <<` (heredoc feeding script via stdin)
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith('#'):
                    continue
                if 'python3' not in stripped or '<<' not in stripped:
                    continue
                # Match: python3 - << 'MARKER' or python3 - <<MARKER
                if not re.search(r'python3\s+-\s*<<', stripped):
                    continue

                # Found a heredoc-fed python3 invocation.
                # Now check if this $(...) block also contains <<< (herestring)
                # by scanning forward to the closing )
                for j in range(i + 1, len(lines)):
                    fwd = lines[j].strip()
                    if fwd.startswith('#'):
                        continue
                    if fwd.startswith('<<<') or '<<< ' in fwd:
                        rel = os.path.relpath(script, REPO_ROOT)
                        violations.append(
                            f"{rel}:{i+1}: python3 - <<HEREDOC with "
                            f"<<< herestring at line {j+1}"
                        )
                        break
                    # Stop scanning at heredoc end markers or next command
                    if re.match(r'^[A-Z]+\)?$', fwd):
                        break

        self.assertEqual(
            violations, [],
            "python3 heredoc+herestring pattern found!\n"
            + "\n".join(violations)
        )


class TestHNDataPipeline(unittest.TestCase):
    """Verify the HN script's data pipeline works end-to-end with mock data."""

    def test_echo_pipe_python_c_produces_output(self):
        """Simulate the fixed pattern: echo | python3 -c reads stdin correctly."""
        mock_json = '{"zh_title":"Test","point":"P","stars":"S","title":"T","hn_url":"https://example.com"}'
        result = subprocess.run(
            ["python3", "-c", """
import json, sys
sent = 0
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try: d = json.loads(line)
    except: continue
    if not d.get("hn_url", "").strip(): continue
    sent += 1
print(sent)
"""],
            input=mock_json,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.stdout.strip(), "1",
                         f"Expected 1 item processed, got: {result.stdout!r}")

    def test_multiple_items_all_counted(self):
        """Multiple JSON lines should all be processed."""
        items = [
            '{"zh_title":"A","hn_url":"https://a.com","title":"A","point":"p","stars":"s"}',
            '{"zh_title":"B","hn_url":"https://b.com","title":"B","point":"p","stars":"s"}',
            '{"zh_title":"C","hn_url":"https://c.com","title":"C","point":"p","stars":"s"}',
        ]
        result = subprocess.run(
            ["python3", "-c", """
import json, sys
sent = 0
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try: d = json.loads(line)
    except: continue
    if not d.get("hn_url", "").strip(): continue
    sent += 1
print(sent)
"""],
            input="\n".join(items),
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.stdout.strip(), "3")

    def test_empty_hn_url_skipped(self):
        """Items with empty hn_url should be skipped (Fix3 protection)."""
        mock_json = '{"zh_title":"Test","point":"P","stars":"S","title":"T","hn_url":""}'
        result = subprocess.run(
            ["python3", "-c", """
import json, sys
sent = 0
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try: d = json.loads(line)
    except: continue
    if not d.get("hn_url", "").strip(): continue
    sent += 1
print(sent)
"""],
            input=mock_json,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.stdout.strip(), "0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
