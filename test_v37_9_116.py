"""V37.9.116 守卫: governance_audit_cron 生产审计必须 CONVERGENCE_DRY_RUN=1 (不静默改 crontab).

触发 (2026-06-05): INV-CRON-004 auto_deploy 双行第 3 次复发 (V37.9.106/111/今天).
用户三次 crontab_safe.sh remove line 54 (~/auto_deploy.sh HOME 裸名), 每次又回来.

数据驱动诊断 (原则 #28, Mac Mini 原子实验铁证):
  bash -c '
    crontab_safe.sh remove "bash ~/auto_deploy.sh >> "
    echo "删除后: $(crontab -l | grep -Fc ...)"          → 0 HOME-bare 行
    python3 ontology/governance_checker.py --full         (no CONVERGENCE_DRY_RUN)
    echo "跑完 gov 后: $(crontab -l | grep -Fc ...)"       → 1 HOME-bare 行  ← 重加!
  '

真根因:
  V37.9.58 切关 convergence dry-run 默认 (机器自愈 machine_sync). governance_audit_cron.sh
  line 70 跑 governance_checker.py --full 时**不设** CONVERGENCE_DRY_RUN → jobs_to_crontab
  spec real-apply. auto_deploy registry entry "auto_deploy.sh" (裸名) 让 _format_cron_line
  生成 ~/auto_deploy.sh (HOME) ≠ canonical crontab line 5 ~/openclaw-model-bridge/auto_deploy.sh
  (repo) → convergence 判 missing → 每次审计 real-apply 重加 line 54 → 用户删了下次又加.

修复 (MR-9 测试污染生产 + V37.9.113 扩到生产审计):
  治理"审计"是观察者 — 必须检测+告警 drift, 不静默 mutate 被审计的系统状态.
  governance_audit_cron.sh line 70 加 CONVERGENCE_DRY_RUN=1 → 07:00 审计只检测告警, 不 apply.
  machine_sync 自愈应是 operator 显式刻意动作 (手动跑无此 env 即 real-apply), 不是审计副作用.
  drift 仍被 governance 检测 (报 ⚠️/❌), 只是不自动改 crontab.

测试契约:
  Layer 1 (源码静态): governance_audit_cron --full 调用必须前置 CONVERGENCE_DRY_RUN=1 + V37.9.116 marker
  Layer 2 (行为级): 验证 CONVERGENCE_DRY_RUN=1 真让 convergence._is_dry_run() 返回 True (env 语义正确)
  Layer 3 (反向验证): 文档化 V37.9.116 前 no-env real-apply 的血案行为
"""
from __future__ import annotations

import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
GOV_AUDIT_SH = os.path.join(REPO_ROOT, "governance_audit_cron.sh")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


class TestV37_9_116_GovAuditDryRun(unittest.TestCase):
    """V37.9.116: governance_audit_cron 跑 governance --full 必须 CONVERGENCE_DRY_RUN=1."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read(GOV_AUDIT_SH)

    def test_governance_full_call_has_convergence_dry_run(self):
        """governance_checker.py --full 调用行必须前置 CONVERGENCE_DRY_RUN=1.

        核心守卫: 找到 `python3 ontology/governance_checker.py --full` 的调用行,
        断言同一命令替换 $(...) 里 python3 之前出现 CONVERGENCE_DRY_RUN=1.
        反 buggy 模式: 不允许裸 `python3 ontology/governance_checker.py --full` (无 env).
        """
        found_full_call = False
        for line in self.src.splitlines():
            stripped = line.split("#", 1)[0]  # 剥行尾注释避免误判
            # 只匹配真实 subprocess 调用行 (含 python3), 跳过 log "..." 日志行
            if "python3" in stripped and "governance_checker.py --full" in stripped:
                found_full_call = True
                self.assertIn(
                    "CONVERGENCE_DRY_RUN=1",
                    stripped,
                    msg=(
                        "governance_checker.py --full 调用必须前置 CONVERGENCE_DRY_RUN=1 "
                        "(V37.9.116: 07:00 审计只检测不静默改 crontab). 实际行: " + line
                    ),
                )
                # env 必须在 python3 之前 (subprocess 环境注入语义)
                env_idx = stripped.index("CONVERGENCE_DRY_RUN=1")
                py_idx = stripped.index("python3")
                self.assertLess(
                    env_idx,
                    py_idx,
                    msg="CONVERGENCE_DRY_RUN=1 必须在 python3 之前 (env 前置注入)",
                )
        self.assertTrue(
            found_full_call,
            msg="未找到 governance_checker.py --full 调用行 (脚本结构变了?)",
        )

    def test_v37_9_116_marker_present(self):
        """V37.9.116 注释 marker 存在 (可追溯)."""
        self.assertIn("V37.9.116", self.src)

    def test_blood_lesson_referenced(self):
        """注释引用 auto_deploy 双行复发血案 + 根因 (运维可 grep)."""
        self.assertIn("auto_deploy", self.src)
        self.assertIn("INV-CRON-004", self.src)
        # 引用 V37.9.58 切关 dry-run 默认是真根因
        self.assertIn("V37.9.58", self.src)

    def test_engine_check_not_falsely_gated(self):
        """engine.py --check 不跑 convergence, 不应被本修复误加 dry-run env.

        防过度修复: V37.9.116 只针对 governance_checker (跑 convergence),
        engine.py --check 是工具本体一致性检查, 与 convergence 无关.
        """
        for line in self.src.splitlines():
            stripped = line.split("#", 1)[0]
            if "engine.py --check" in stripped:
                # engine.py --check 行不应被加 CONVERGENCE_DRY_RUN (无关)
                self.assertNotIn(
                    "CONVERGENCE_DRY_RUN",
                    stripped,
                    msg="engine.py --check 不跑 convergence, 不应加 dry-run env (过度修复)",
                )


class TestV37_9_116_DryRunEnvSemantics(unittest.TestCase):
    """V37.9.116 行为级: 验证 CONVERGENCE_DRY_RUN=1 真让 convergence dry-run."""

    def test_env_makes_convergence_dry_run(self):
        """convergence._is_dry_run() 在 CONVERGENCE_DRY_RUN=1 时返回 True.

        V37.9.158: 默认已是 dry-run (MR-23 audit-observes-never-mutates), CONVERGENCE_DRY_RUN=1
        是 force-override (显式 safety belt-and-suspenders). governance_audit 保留它作显式安全.
        """
        import sys
        sys.path.insert(0, os.path.join(REPO_ROOT, "ontology"))
        import convergence as cv

        saved_dry = os.environ.get("CONVERGENCE_DRY_RUN")
        saved_apply = os.environ.get("CONVERGENCE_APPLY")
        try:
            os.environ["CONVERGENCE_DRY_RUN"] = "1"
            os.environ.pop("CONVERGENCE_APPLY", None)
            self.assertTrue(
                cv._is_dry_run(),
                msg="CONVERGENCE_DRY_RUN=1 必须让 _is_dry_run() 返回 True (force-override)",
            )
            # V37.9.158: 未设 env → 默认 dry-run (观察绝不 mutate, MR-23). 这正是 V37.9.158
            # 根治 — 手动/审计 governance 不再因默认 real-apply 而重加 crontab (INV-CRON-004 复发).
            os.environ.pop("CONVERGENCE_DRY_RUN", None)
            os.environ.pop("CONVERGENCE_APPLY", None)
            self.assertTrue(
                cv._is_dry_run(),
                msg="V37.9.158: 未设 env → 默认 dry-run (观察绝不 mutate). real-apply 现需显式 "
                "CONVERGENCE_APPLY=1; governance_audit 的 CONVERGENCE_DRY_RUN=1 是冗余 safety override.",
            )
        finally:
            for _k, _v in (("CONVERGENCE_DRY_RUN", saved_dry),
                           ("CONVERGENCE_APPLY", saved_apply)):
                if _v is None:
                    os.environ.pop(_k, None)
                else:
                    os.environ[_k] = _v


class TestV37_9_116_ShellSyntax(unittest.TestCase):
    """脚本语法仍有效."""

    def test_bash_n_passes(self):
        import subprocess
        r = subprocess.run(
            ["bash", "-n", GOV_AUDIT_SH],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, msg=f"bash -n 失败: {r.stderr}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
