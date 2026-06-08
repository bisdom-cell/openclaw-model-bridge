"""test_v37_9_122_filemap_exemption_convergence.py — V37.9.122 日落法收敛守卫

#27 FILE_MAP 豁免一物多形收敛 (原则 #34 日落法 MR-22 + 原则 #31 跨消费者豁免漏同步):
RUNS_FROM_REPO_CLONE 豁免此前在 3 个 Python 消费者各自硬编码 'auto_deploy.sh' 字面量
(path_consistency_scanner 权威源 + preflight check 15 ×3 + check_registry 漏豁免),
V37.9.122 收敛到 path_consistency_scanner 单一真理源, 其他消费者 import 复用.

修复的真潜伏 bug: V37.9.120 退役 auto_deploy 出 FILE_MAP 时漏同步
check_registry.check_filemap_completeness, 让 `check_registry.py --check-crontab`
仍硬性报 "[auto_deploy] 'auto_deploy.sh' 存在于仓库但不在 FILE_MAP". 这是
V37.8.11/V37.8.13/V37.9.120/V37.9.121-hotfix 反复"跨消费者豁免漏同步"血案类的活样本.

测试维度:
  - 单一真理源契约 (RUNS_FROM_REPO_CLONE 含 auto_deploy.sh + 每条带 reason)
  - check_registry 行为修复 (不再误报) + 反向验证 (豁免空则误报, 证豁免真做活)
  - check_registry / preflight check 15 源码守卫 (引用单一源, 无硬编码字面量)
  - preflight check 15 行为 (forward 不 flag + 反向 sabotaged 空豁免真有效)
"""
import inspect
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_DIR = os.path.dirname(os.path.abspath(__file__))

# 真 repo crontab 行 (auto_deploy 从 repo clone 跑, 不在 FILE_MAP)
_AUTO_DEPLOY_CRON = (
    "*/2 * * * * bash -lc 'bash ~/openclaw-model-bridge/auto_deploy.sh "
    ">> ~/.openclaw/logs/auto_deploy.log 2>&1'"
)


def _extract_check15_block():
    """从 preflight_check.sh 提取 check 15 inline Python heredoc 块."""
    with open(os.path.join(REPO_DIR, "preflight_check.sh"), encoding="utf-8") as f:
        content = f.read()
    blocks = re.findall(r"<< 'PYEOF'\n(.*?)\nPYEOF", content, re.DOTALL)
    # check 15 唯一锚点: file_map_srcs
    check15 = [b for b in blocks if "file_map_srcs" in b]
    assert len(check15) == 1, f"check 15 PYEOF 块定位失败 (找到 {len(check15)} 个)"
    return check15[0]


def _run_check15(block, repo_dir, crontab_lines):
    """以 preflight 同款方式运行提取的 check 15 inline Python, 注入 fake crontab.

    返回 stdout+stderr 合并字符串.
    """
    with tempfile.TemporaryDirectory() as bindir:
        crontab_path = os.path.join(bindir, "crontab")
        with open(crontab_path, "w", encoding="utf-8") as f:
            f.write("#!/usr/bin/env bash\ncat <<'CRONEOF'\n")
            f.write("\n".join(crontab_lines))
            f.write("\nCRONEOF\n")
        os.chmod(crontab_path, 0o755)
        env = dict(os.environ)
        env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
        proc = subprocess.run(
            [sys.executable, "-",
             os.path.join(repo_dir, "auto_deploy.sh"),
             os.path.join(repo_dir, "jobs_registry.yaml")],
            input=block, capture_output=True, text=True, env=env, timeout=30,
        )
        return proc.stdout + proc.stderr


class TestV37_9_122_SingleSourceContract(unittest.TestCase):
    """path_consistency_scanner.RUNS_FROM_REPO_CLONE 是唯一真理源."""

    def test_run_from_repo_clone_contains_auto_deploy(self):
        from path_consistency_scanner import RUNS_FROM_REPO_CLONE
        self.assertIn("auto_deploy.sh", RUNS_FROM_REPO_CLONE)

    def test_every_exemption_has_explicit_reason(self):
        # 拒绝"无理由豁免"防止豁免清单成为漏洞兜底 (path_consistency_scanner 头注释契约).
        from path_consistency_scanner import RUNS_FROM_REPO_CLONE
        for name, reason in RUNS_FROM_REPO_CLONE.items():
            self.assertTrue(
                reason and isinstance(reason, str) and len(reason) > 10,
                f"{name} 豁免必须有显式 reason, got {reason!r}",
            )


class TestV37_9_122_CheckRegistryConvergence(unittest.TestCase):
    """check_registry.check_filemap_completeness 收敛 + 潜伏 bug 修复."""

    def test_filemap_does_not_flag_auto_deploy(self):
        """潜伏 bug 修复: 真 jobs_registry 跑 check, auto_deploy 不被误报不在 FILE_MAP."""
        import check_registry
        registry = os.path.join(REPO_DIR, "jobs_registry.yaml")
        errors, _ = check_registry.check_filemap_completeness(registry)
        auto_deploy_errs = [e for e in errors if "auto_deploy.sh" in e]
        self.assertEqual(
            auto_deploy_errs, [],
            f"auto_deploy 不应被误报不在 FILE_MAP (V37.9.120 漏同步潜伏 bug): {auto_deploy_errs}",
        )

    def test_reverse_validation_empty_exemption_flags_auto_deploy(self):
        """反向验证: RUNS_FROM_REPO_CLONE 置空 → auto_deploy 重新被 flag (证豁免做真活)."""
        import path_consistency_scanner as pcs
        import check_registry
        original = pcs.RUNS_FROM_REPO_CLONE
        try:
            pcs.RUNS_FROM_REPO_CLONE = {}
            registry = os.path.join(REPO_DIR, "jobs_registry.yaml")
            errors, _ = check_registry.check_filemap_completeness(registry)
            self.assertTrue(
                any("auto_deploy.sh" in e for e in errors),
                "豁免空集时 auto_deploy 应被 flag — 反证 check_registry 真读单一真理源",
            )
        finally:
            pcs.RUNS_FROM_REPO_CLONE = original

    def test_source_references_single_source_no_literal(self):
        """源码守卫: 函数体引用 RUNS_FROM_REPO_CLONE, 无硬编码 == 'auto_deploy.sh'."""
        import check_registry
        src = inspect.getsource(check_registry.check_filemap_completeness)
        self.assertIn("RUNS_FROM_REPO_CLONE", src, "应从单一真理源导入豁免")
        self.assertNotIn("== 'auto_deploy.sh'", src, "不得硬编码 auto_deploy 字面量")
        self.assertNotIn('== "auto_deploy.sh"', src)
        self.assertIn("V37.9.122", src)


class TestV37_9_122_PreflightCheck15Convergence(unittest.TestCase):
    """preflight check 15 inline Python 收敛守卫 (源码 + 行为)."""

    def test_check15_uses_single_source_no_literal(self):
        block = _extract_check15_block()
        # 代码行 (剥注释) 不得有硬编码 auto_deploy.sh 豁免字面量
        code = "\n".join(
            ln for ln in block.splitlines() if not ln.strip().startswith("#")
        )
        self.assertNotIn("== 'auto_deploy.sh'", code, "check 15 代码行不得硬编码字面量")
        self.assertNotIn('== "auto_deploy.sh"', code)
        # 导入单一真理源 + 3 处豁免决策用 in RUNS_FROM_REPO_CLONE
        self.assertIn(
            "from path_consistency_scanner import RUNS_FROM_REPO_CLONE", block
        )
        self.assertGreaterEqual(
            code.count("in RUNS_FROM_REPO_CLONE"), 3,
            "check 15 应有 3 处豁免决策 (forward/reverse crontab + registry cross) 用 in RUNS_FROM_REPO_CLONE",
        )
        self.assertIn("V37.9.122", block)

    def test_check15_behavioral_forward_no_auto_deploy_flag(self):
        """行为(forward): 真 repo + 含 auto_deploy 的 crontab, auto_deploy 不被 flag."""
        block = _extract_check15_block()
        out = _run_check15(block, repo_dir=REPO_DIR, crontab_lines=[_AUTO_DEPLOY_CRON])
        # import 真解析 (无 FAIL-OPEN WARN)
        self.assertNotIn(
            "path_consistency_scanner 导入失败", out,
            "import 应真解析, 不走 FAIL-OPEN 空集分支",
        )
        flags = [
            ln for ln in out.splitlines()
            if "auto_deploy" in ln and ("FAIL" in ln or "不在 FILE_MAP" in ln)
        ]
        self.assertEqual(flags, [], f"auto_deploy 不应被 flag: {flags}")

    def test_check15_behavioral_reverse_sabotaged_exemption_flags(self):
        """行为(反向): fake 空 RUNS_FROM_REPO_CLONE → auto_deploy 被 flag (证 import 真做活)."""
        block = _extract_check15_block()
        with tempfile.TemporaryDirectory() as td:
            shutil.copy(os.path.join(REPO_DIR, "auto_deploy.sh"), td)
            shutil.copy(os.path.join(REPO_DIR, "jobs_registry.yaml"), td)
            # sabotaged single-source: 空豁免集
            with open(os.path.join(td, "path_consistency_scanner.py"), "w",
                      encoding="utf-8") as f:
                f.write("RUNS_FROM_REPO_CLONE = {}\n")
            out = _run_check15(block, repo_dir=td, crontab_lines=[_AUTO_DEPLOY_CRON])
            self.assertTrue(
                any("auto_deploy" in ln for ln in out.splitlines()),
                "空豁免时 auto_deploy 应被 flag — 反证 check 15 真读单一真理源 import",
            )


class TestV37_9_122_Marker(unittest.TestCase):
    def test_markers_present(self):
        for fname in ("check_registry.py", "preflight_check.sh"):
            with open(os.path.join(REPO_DIR, fname), encoding="utf-8") as f:
                self.assertIn(
                    "V37.9.122", f.read(), f"{fname} 缺 V37.9.122 收敛 marker"
                )


if __name__ == "__main__":
    unittest.main()
