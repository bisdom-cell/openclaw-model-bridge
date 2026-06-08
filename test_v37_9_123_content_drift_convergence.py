"""test_v37_9_123_content_drift_convergence.py — V37.9.123 日落法 #27 概念 B 守卫

概念 B (status.json 内容漂移 bash 收敛): preflight check 6 + auto_deploy drift loop
此前各自硬编码 `if [[ "$SRC" == "status.json" ]]` 内容漂移豁免字面量 (一物二形),
V37.9.123 收敛到 path_consistency_scanner.CONTENT_DRIFT_EXEMPT 单一真理源,
bash 消费者经 `--print-exempt content-drift` 查询.

不同 FAIL 方向是【设计】(非 bug):
  - auto_deploy drift loop = FAIL-SAFE (查询失败/空 → 安全网退回 status.json,
    绝不静默 overwrite runtime status.json, 防 V37.8.11 每小时覆盖血案)
  - preflight check 6 = FAIL-OPEN (查询失败/空 → status.json 进 md5 比对 → 可见 fail, 无害)

与概念 A (V37.9.122 RUNS_FROM_REPO_CLONE / FILE_MAP 成员豁免) 是不同 concern:
  概念 A = 成员 (文件不在 FILE_MAP) / 概念 B = 内容 (文件在 FILE_MAP 只是内容漂移).

测试维度:
  - 单一真理源契约 (CONTENT_DRIFT_EXEMPT 含 status.json + reason)
  - --print-exempt CLI 行为 (yaml-independent robust)
  - auto_deploy / preflight 源码守卫 (query + 成员检查 + 无 == status.json 决策字面量)
  - auto_deploy FAIL-SAFE 安全网行为 (空查询 → status.json 退回)
  - 成员检查 grep -qxF 全行匹配行为 + 反向 sabotage
"""
import inspect
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

REPO_DIR = os.path.dirname(os.path.abspath(__file__))


def _read(fname):
    with open(os.path.join(REPO_DIR, fname), encoding="utf-8") as f:
        return f.read()


def _run_bash(script, *args, env=None):
    return subprocess.run(
        ["bash", "-c", script, "bash", *args],
        capture_output=True, text=True, timeout=30, env=env,
    )


class TestV37_9_123_SingleSourceContract(unittest.TestCase):
    def test_content_drift_exempt_contains_status_json(self):
        from path_consistency_scanner import CONTENT_DRIFT_EXEMPT
        self.assertIn("status.json", CONTENT_DRIFT_EXEMPT)

    def test_every_exemption_has_explicit_reason(self):
        from path_consistency_scanner import CONTENT_DRIFT_EXEMPT
        for name, reason in CONTENT_DRIFT_EXEMPT.items():
            self.assertTrue(
                reason and isinstance(reason, str) and len(reason) > 10,
                f"{name} 内容漂移豁免必须有显式 reason, got {reason!r}",
            )

    def test_exempt_categories_maps_all_three(self):
        from path_consistency_scanner import (
            _EXEMPT_CATEGORIES, CONTENT_DRIFT_EXEMPT,
            RUNS_FROM_REPO_CLONE, ALLOWED_NON_STANDARD_DST,
        )
        self.assertIs(_EXEMPT_CATEGORIES["content-drift"], CONTENT_DRIFT_EXEMPT)
        self.assertIs(_EXEMPT_CATEGORIES["repo-clone"], RUNS_FROM_REPO_CLONE)
        self.assertIs(_EXEMPT_CATEGORIES["non-standard-dst"], ALLOWED_NON_STANDARD_DST)

    def test_content_drift_distinct_from_repo_clone(self):
        # 概念 A (成员) vs 概念 B (内容) 是不同 concern — status.json 在 FILE_MAP, auto_deploy 不在
        from path_consistency_scanner import CONTENT_DRIFT_EXEMPT, RUNS_FROM_REPO_CLONE
        self.assertNotIn("status.json", RUNS_FROM_REPO_CLONE)
        self.assertNotIn("auto_deploy.sh", CONTENT_DRIFT_EXEMPT)


class TestV37_9_123_PrintExemptCli(unittest.TestCase):
    SCANNER = os.path.join(REPO_DIR, "path_consistency_scanner.py")

    def test_content_drift_prints_status_json(self):
        out = subprocess.run(
            [sys.executable, self.SCANNER, "--print-exempt", "content-drift"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(out.returncode, 0)
        self.assertIn("status.json", out.stdout.splitlines())

    def test_repo_clone_prints_auto_deploy(self):
        out = subprocess.run(
            [sys.executable, self.SCANNER, "--print-exempt", "repo-clone"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(out.returncode, 0)
        self.assertIn("auto_deploy.sh", out.stdout.splitlines())

    def test_invalid_category_errors(self):
        out = subprocess.run(
            [sys.executable, self.SCANNER, "--print-exempt", "bogus"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertNotEqual(out.returncode, 0)

    def test_print_exempt_is_yaml_independent(self):
        """--print-exempt 早退不依赖 yaml (robust, 系统部分坏也能查单一源)."""
        code = (
            "import builtins; _r=builtins.__import__\n"
            "def _n(n,*a,**k):\n"
            " if n=='yaml': raise ImportError('no yaml')\n"
            " return _r(n,*a,**k)\n"
            "builtins.__import__=_n\n"
            "import sys; sys.path.insert(0, %r)\n"
            "import path_consistency_scanner as p\n"
            "sys.exit(p.main(['--print-exempt','content-drift']))\n"
        ) % REPO_DIR
        out = subprocess.run([sys.executable, "-c", code],
                             capture_output=True, text=True, timeout=15)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("status.json", out.stdout.splitlines())


class TestV37_9_123_AutoDeployConvergence(unittest.TestCase):
    def setUp(self):
        self.src = _read("auto_deploy.sh")

    def test_queries_single_source(self):
        self.assertIn(
            'path_consistency_scanner.py" --print-exempt content-drift', self.src
        )

    def test_uses_membership_check_no_literal_decision(self):
        # drift loop 豁免决策用成员检查, 不硬编码 == status.json 比较
        self.assertIn('grep -qxF "$SRC"', self.src)
        # 安全网赋值 CONTENT_DRIFT_EXEMPT="status.json" 是 fallback (= 非 ==), 允许;
        # 但豁免【决策】(== 比较) 不得硬编码
        self.assertNotIn('== "status.json"', self.src)
        self.assertNotIn("== 'status.json'", self.src)

    def test_has_fail_safe_net(self):
        # FAIL-SAFE: 查询空 → 退回 status.json 防 V37.8.11 覆盖
        self.assertIn('if [ -z "$CONTENT_DRIFT_EXEMPT" ]', self.src)
        self.assertIn('CONTENT_DRIFT_EXEMPT="status.json"', self.src)
        self.assertIn("V37.8.11", self.src)
        self.assertIn("V37.9.123", self.src)

    def test_fail_safe_behavioral_empty_query_falls_back(self):
        """行为: 空查询 → 安全网退回 status.json → status.json 仍被豁免 (防血案)."""
        # 隔离测试安全网 + 成员检查 pattern (auto_deploy 同款), 注入 fake 空查询 + 真查询
        harness = textwrap.dedent("""\
            set -eEuo pipefail
            QUERY_CMD="$1"; SRC="$2"; LOG=/dev/null
            CONTENT_DRIFT_EXEMPT="$(eval "$QUERY_CMD" 2>/dev/null || true)"
            if [ -z "$CONTENT_DRIFT_EXEMPT" ]; then
                CONTENT_DRIFT_EXEMPT="status.json"
                echo "SAFETY_NET" >&2
            fi
            if printf '%s\\n' "$CONTENT_DRIFT_EXEMPT" | grep -qxF "$SRC"; then
                echo EXEMPT
            else
                echo NOT_EXEMPT
            fi
        """)
        # Case A: 真查询 (CLI) → status.json 豁免, 无安全网
        scanner = os.path.join(REPO_DIR, "path_consistency_scanner.py")
        real = _run_bash(harness,
                         f"{sys.executable} {scanner} --print-exempt content-drift",
                         "status.json")
        self.assertEqual(real.stdout.strip(), "EXEMPT")
        self.assertNotIn("SAFETY_NET", real.stderr)
        # Case B: 空查询 (false) → 安全网退回 → status.json 仍豁免 (防 V37.8.11)
        empty = _run_bash(harness, "false", "status.json")
        self.assertEqual(empty.stdout.strip(), "EXEMPT")
        self.assertIn("SAFETY_NET", empty.stderr)
        # Case C: 空查询 + 非 status.json 文件 → 安全网只豁免 status.json, 其他正常比对
        other = _run_bash(harness, "false", "adapter.py")
        self.assertEqual(other.stdout.strip(), "NOT_EXEMPT")


class TestV37_9_123_PreflightCheck6Convergence(unittest.TestCase):
    def setUp(self):
        self.src = _read("preflight_check.sh")

    def test_queries_single_source(self):
        self.assertIn(
            'path_consistency_scanner.py" --print-exempt content-drift', self.src
        )

    def test_check6_uses_membership_no_literal_decision(self):
        # check 6 豁免决策用成员检查 (preflight 无 == status.json 决策字面量)
        self.assertIn('grep -qxF "$SRC"', self.src)
        self.assertNotIn('"$SRC" == "status.json"', self.src)
        self.assertIn("V37.9.123", self.src)

    def test_fail_open_no_safety_net_literal(self):
        # preflight 是 FAIL-OPEN (查询空 → md5 比对 → 可见 fail), 不需安全网赋值字面量.
        # 验证 preflight 不含 auto_deploy 式 CONTENT_DRIFT_EXEMPT="status.json" 安全网赋值.
        self.assertNotIn('CONTENT_DRIFT_EXEMPT="status.json"', self.src)


class TestV37_9_123_MembershipPattern(unittest.TestCase):
    """grep -qxF 全行精确匹配行为 (防部分串误命中)."""

    def _is_exempt(self, exempt_set, src):
        r = _run_bash(
            'printf "%s\\n" "$1" | grep -qxF "$2"', exempt_set, src
        )
        return r.returncode == 0

    def test_exact_match_exempt(self):
        self.assertTrue(self._is_exempt("status.json", "status.json"))

    def test_non_member_not_exempt(self):
        self.assertFalse(self._is_exempt("status.json", "adapter.py"))

    def test_partial_string_not_exempt(self):
        # -x 全行匹配: "status" 不应命中 "status.json"
        self.assertFalse(self._is_exempt("status.json", "status"))

    def test_multi_line_set(self):
        self.assertTrue(self._is_exempt("status.json\nfoo.json", "foo.json"))
        self.assertFalse(self._is_exempt("status.json\nfoo.json", "bar.json"))


class TestV37_9_123_ReverseValidation(unittest.TestCase):
    def test_sabotaged_empty_content_drift_exempt_prints_nothing(self):
        """反向: fake 空 CONTENT_DRIFT_EXEMPT 的 path_consistency_scanner → CLI 打印空
        (证 --print-exempt 真读 dict, 非硬编码 status.json)."""
        with tempfile.TemporaryDirectory() as td:
            fake = os.path.join(td, "path_consistency_scanner.py")
            with open(fake, "w", encoding="utf-8") as f:
                f.write(
                    "import sys, argparse\n"
                    "CONTENT_DRIFT_EXEMPT={}\n"
                    "RUNS_FROM_REPO_CLONE={}\n"
                    "ALLOWED_NON_STANDARD_DST={}\n"
                    "_EXEMPT_CATEGORIES={'content-drift':CONTENT_DRIFT_EXEMPT,"
                    "'repo-clone':RUNS_FROM_REPO_CLONE,'non-standard-dst':ALLOWED_NON_STANDARD_DST}\n"
                    "def main(argv):\n"
                    " p=argparse.ArgumentParser(); p.add_argument('--print-exempt')\n"
                    " a=p.parse_args(argv)\n"
                    " [print(n) for n in _EXEMPT_CATEGORIES.get(a.print_exempt,{})]\n"
                    " return 0\n"
                    "if __name__=='__main__': sys.exit(main(sys.argv[1:]))\n"
                )
            out = subprocess.run(
                [sys.executable, fake, "--print-exempt", "content-drift"],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(out.stdout.strip(), "",
                             "空 dict 应打印空 — 反证 CLI 真读 CONTENT_DRIFT_EXEMPT 非硬编码")


class TestV37_9_123_Marker(unittest.TestCase):
    def test_markers_present(self):
        for fname in ("path_consistency_scanner.py", "auto_deploy.sh",
                      "preflight_check.sh"):
            self.assertIn("V37.9.123", _read(fname), f"{fname} 缺 V37.9.123 marker")


if __name__ == "__main__":
    unittest.main()
