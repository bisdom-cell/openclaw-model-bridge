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
    "jobs/hn_watcher/run_hn_fixed.sh",
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


# ── V37.9.357: 命令替换 $( ) 内部的 \" 不是转义 ─────────────────────────
#
# 血案（2026-09-22，从 freight_watcher.log 取证的上下文行里撞见）：
#   5 个 job 脚本在 python3 -c "..." 双引号串里写
#       sys.path.insert(0, '$(cd \"$(dirname \"$0\")\" && pwd)')
#   作者以为身处双引号内所以给内层引号加了反斜杠，但 bash 对 $( ) 内部的文本
#   不做外层引号的反斜杠处理——反斜杠原样进入子命令，于是 cd 收到的参数是
#   带字面引号的 `""/Users/bisdom/.openclaw/jobs/freight_watcher"`，
#   每次运行 stderr 落一行 `cd: ... No such file or directory`，命令替换
#   结果为空 → sys.path.insert(0, '')，脚本目录 fallback 自 V37.9.57 起从未生效。
#   生产靠 FILE_MAP 部署的 ~/hallucination_guards.py 兜住，所以 prompt 内容未受
#   影响；代价是 5 份日志每天一行假「目录不存在」（watchdog err_pattern 不匹配
#   该句，故从未告警）。其余 13 个同款消费者写的是不带反斜杠的正确形态。
#   MR-8 copy-paste-is-a-bug-class：同一段 wiring 两种拼法，错的那种潜伏四个月。
_ESCAPED_CD_IN_SUBST = re.compile(r'\$\(cd \\"')
_WORKING_CD_DIRNAME = '$(cd "$(dirname "$0")" && pwd)'
_HG_BLOCK_OPEN = re.compile(r'^(HG_[A-Z0-9_]+)=\$\(python3 -c "$')
_HG_BLOCK_CLOSE = re.compile(r'^" 2>/dev/null\)$')


def _all_repo_sh():
    out = []
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules")]
        for fn in files:
            if fn.endswith(".sh") and not fn.startswith("test_"):
                out.append(os.path.join(root, fn))
    return sorted(out)


def _extract_hg_block(path):
    """抽出 HG_*=$(python3 -c " ... " 2>/dev/null) 整块（含首尾行），找不到返回 None。"""
    lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    for i, line in enumerate(lines):
        if _HG_BLOCK_OPEN.match(line):
            for j in range(i + 1, min(i + 40, len(lines))):
                if _HG_BLOCK_CLOSE.match(lines[j]):
                    return _HG_BLOCK_OPEN.match(line).group(1), "\n".join(lines[i:j + 1])
    return None


class TestNoEscapedQuotesInsideCommandSubstitution(unittest.TestCase):
    """V37.9.357 守卫：$( ) 内不得出现 \\" 转义形态的 cd（每次运行必失败）。"""

    FORMERLY_BROKEN = [
        "jobs/hn_watcher/run_hn_fixed.sh",
        "jobs/ontology_sources/run_ontology_sources.sh",
        "jobs/finance_news/run_finance_news.sh",
        "jobs/openclaw_official/run_discussions.sh",
        "jobs/freight_watcher/run_freight.sh",
    ]

    def test_escaped_cd_inside_command_substitution_retired(self):
        hits = []
        for path in _all_repo_sh():
            for n, line in enumerate(open(path, encoding="utf-8", errors="replace"), 1):
                if _ESCAPED_CD_IN_SUBST.search(line):
                    hits.append(f"{os.path.relpath(path, REPO_ROOT)}:{n}: {line.strip()}")
        self.assertEqual(hits, [], "命令替换内部的 \\\" 不是转义，cd 必失败:\n" + "\n".join(hits))

    def test_working_form_is_the_repo_convention(self):
        """防空转：正确形态必须真的在仓库里大量存在，否则上一条守卫守的是空气。"""
        users = [p for p in _all_repo_sh()
                 if _WORKING_CD_DIRNAME in open(p, encoding="utf-8", errors="replace").read()]
        self.assertGreaterEqual(len(users), 10, users)
        for rel in self.FORMERLY_BROKEN:
            self.assertIn(os.path.join(REPO_ROOT, rel), users, rel)

    def test_reverse_evidence_escaped_form_fails_working_form_resolves(self):
        """用真 bash 证明规则不是风格偏好：转义形态 cd 报错且结果为空，正确形态得到脚本目录。"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            for form, expect_dir, expect_err in (
                ('$(cd \\"$(dirname \\"$0\\")\\" && pwd)', False, True),
                ('$(cd "$(dirname "$0")" && pwd)', True, False),
            ):
                script = os.path.join(td, "probe.sh")
                with open(script, "w") as fh:
                    fh.write('X=$(python3 -c "\nprint(\'' + form + '\')\n")\nprintf \'%s\' "$X"\n')
                r = subprocess.run(["bash", script], capture_output=True, text=True, cwd="/")
                self.assertEqual(r.stdout == os.path.realpath(td) or r.stdout == td, expect_dir, (form, r.stdout))
                self.assertEqual("No such file or directory" in r.stderr, expect_err, (form, r.stderr))

    def test_hg_block_resolves_script_dir_blood_case(self):
        """行为级：从 5 个脚本抽真实 HG 块，放到带假 hallucination_guards.py 的目录里跑，
        HOME 为空目录、cwd 不在脚本目录 → 只有脚本目录 fallback 生效时才拿得到 guard 文本。
        修复前：cd 报错 + sys.path 拿到 '' → import 失败 → 空串。"""
        import tempfile
        for rel in self.FORMERLY_BROKEN:
            extracted = _extract_hg_block(os.path.join(REPO_ROOT, rel))
            self.assertIsNotNone(extracted, f"{rel}: 找不到 HG_*=$(python3 -c 块，抽取器需更新")
            var, block = extracted
            with tempfile.TemporaryDirectory() as sd, tempfile.TemporaryDirectory() as home:
                with open(os.path.join(sd, "hallucination_guards.py"), "w") as fh:
                    fh.write("def get_guard(level):\n    return 'FROM_SCRIPT_DIR:' + level\n")
                script = os.path.join(sd, "probe.sh")
                with open(script, "w") as fh:
                    fh.write(block + '\nprintf \'%s\' "$' + var + '"\n')
                env = {"HOME": home, "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
                r = subprocess.run(["bash", script], capture_output=True, text=True, cwd=home, env=env)
                self.assertTrue(r.stdout.startswith("FROM_SCRIPT_DIR:"), (rel, r.stdout, r.stderr))
                self.assertNotIn("No such file or directory", r.stderr, (rel, r.stderr))


if __name__ == "__main__":
    unittest.main(verbosity=2)
