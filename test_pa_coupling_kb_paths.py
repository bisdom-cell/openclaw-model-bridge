#!/usr/bin/env python3
"""test_pa_coupling_kb_paths.py — V37.9.249 PA 耦合盘点 (H1-B / B1) 第一批 config 化守卫

背景: docs/pa_coupling_inventory.md §5 — 3 个 KB 脚本的 KB base 默认值从个人路径
`/Users/bisdom/.kb` 收敛到可移植的 `$HOME/.kb`（对齐 15+ job 脚本 + auto_deploy FILE_MAP，
一物一形 + 可迁移性）。Mac Mini 上 $HOME=/Users/bisdom → 行为逐字节不变。

这是 B3「新增 PA-specific 硬编码有扫描器拦截」的**定向种子**（未框架化整个扫描器，
日落法：不为 B1 造新机器）。反向验证：把默认值改回 `/Users/bisdom` → 守卫立即 FAIL。
"""
import os
import re
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))

# 3 个本次收敛的 KB 脚本 (docs/pa_coupling_inventory.md §5 A1)
_CONVERGED_SCRIPTS = ("kb_write.sh", "kb_search.sh", "kb_inject.sh")

# KB base 默认赋值行: KB_BASE / KB_DIR = "${KB_BASE:-<default>}"
_KB_DEFAULT_RE = re.compile(
    r'^\s*KB_(?:BASE|DIR)="\$\{KB_BASE:-([^}]+)\}"', re.MULTILINE
)


def _read(name):
    with open(os.path.join(REPO, name), encoding="utf-8") as f:
        return f.read()


class TestKbScriptDefaultsPortable(unittest.TestCase):
    """3 个 KB 脚本默认值必须可移植 ($HOME/.kb), 不得回退个人路径."""

    def test_default_is_home_kb(self):
        for name in _CONVERGED_SCRIPTS:
            content = _read(name)
            m = _KB_DEFAULT_RE.search(content)
            self.assertIsNotNone(
                m, f"{name}: 找不到 KB_BASE/KB_DIR 默认赋值行 (格式漂移?)"
            )
            self.assertEqual(
                m.group(1).strip(),
                "$HOME/.kb",
                f"{name}: KB base 默认值应为 $HOME/.kb, 实际={m.group(1)!r}",
            )

    def test_no_hardcoded_personal_path_default(self):
        # 反向验证核心: 个人路径绝不能作为默认值回退
        for name in _CONVERGED_SCRIPTS:
            content = _read(name)
            self.assertNotIn(
                "KB_BASE:-/Users/bisdom",
                content,
                f"{name}: 个人路径 /Users/bisdom 不得作为 KB base 默认值 "
                f"(可移植性回退, 见 docs/pa_coupling_inventory.md §5)",
            )

    def test_consistent_with_job_scripts(self):
        # 一物一形: 收敛后应与 job 脚本的 ${KB_BASE:-$HOME/.kb} 形式一致
        sample_job = os.path.join(REPO, "jobs", "dblp", "run_dblp.sh")
        if os.path.exists(sample_job):
            with open(sample_job, encoding="utf-8") as f:
                job = f.read()
            self.assertIn(
                "${KB_BASE:-$HOME/.kb}",
                job,
                "参照 job 脚本 (run_dblp.sh) 应已用 $HOME/.kb — 若变则本守卫的对齐前提失效",
            )
        for name in _CONVERGED_SCRIPTS:
            self.assertIn("${KB_BASE:-$HOME/.kb}", _read(name), f"{name} 未对齐 $HOME/.kb 形式")


class TestSourceGuardMarker(unittest.TestCase):
    def test_marker(self):
        # 守卫存在性自证 (dark-test guard 会核对本文件已注册 full_regression)
        self.assertTrue(all(os.path.exists(os.path.join(REPO, n)) for n in _CONVERGED_SCRIPTS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
