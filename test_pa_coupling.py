#!/usr/bin/env python3
"""test_pa_coupling.py — V37.9.249 PA 耦合盘点 (H1-B / B1) 第一批 config 化守卫

背景: docs/pa_coupling_inventory.md — 边界内 PA 耦合「明确标注 + config 化」。
本批 (第一批, 最低 blast-radius) 落地三项, Mac Mini 上全部生产 no-op（$HOME=/Users/bisdom
+ SYSTEM_TZ 未设 → Asia/Hong_Kong），非-Mac/非-HK 实例才 config 覆盖:

  A1  kb_write/search/inject.sh 默认 /Users/bisdom/.kb → $HOME/.kb（对齐 15+ job 脚本）
  D3  35 个运行时 .sh 的 TZ=Asia/Hong_Kong → TZ=${SYSTEM_TZ:-Asia/Hong_Kong}（默认仍 HKT）
  A2  diagnose.sh openclaw.json 路径 /Users/bisdom/.openclaw → $HOME/.openclaw

**V37.9.251（B3 收尾）**：升级为**全仓 PA 硬编码漂移守卫**——除守护已修的三批，
新增 `TestNoPersonalPathHardcode` 扫**所有运行时 .py+.sh**（非仅已修文件），拦截未来任何
新写的 `/Users/<真实用户名>` 个人路径硬编码（示例名 foo/test 白名单豁免）。这兑现纲领
执行计划 §1 B3 done-criteria「新增 PA-specific 硬编码有 scanner 拦截」——轻量版（扩既有
守卫为全仓扫描，不造新扫描器模块，日落法：复用而非新机器）。

反向验证：任一硬编码回退 / 任一文件新写个人路径 → 守卫立即 FAIL。
"""
import glob
import os
import re
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))

# --- A1: 3 个本次收敛的 KB 脚本 (inventory §5) ---
_CONVERGED_KB_SCRIPTS = ("kb_write.sh", "kb_search.sh", "kb_inject.sh")
_KB_DEFAULT_RE = re.compile(
    r'^\s*KB_(?:BASE|DIR)="\$\{KB_BASE:-([^}]+)\}"', re.MULTILINE
)

# --- D3: TZ 硬编码个人时区 (bare form) vs config 化 form ---
_BARE_TZ = "TZ=Asia/Hong_Kong"
_CONFIG_TZ = "TZ=${SYSTEM_TZ:-Asia/Hong_Kong}"


def _read(name):
    with open(os.path.join(REPO, name), encoding="utf-8") as f:
        return f.read()


def _runtime_shell_scripts():
    """所有运行时 .sh (root + jobs/), 排除 test_ 前缀 (无 .sh 测试, 但保守过滤)."""
    files = glob.glob(os.path.join(REPO, "*.sh"))
    files += glob.glob(os.path.join(REPO, "jobs", "**", "*.sh"), recursive=True)
    return sorted(f for f in files if not os.path.basename(f).startswith("test_"))


def _runtime_code_files():
    """所有运行时 .py + .sh (root + jobs/), 排除 test_ 前缀 (测试 fixture 允许个人路径)."""
    files = []
    for ext in ("*.py", "*.sh"):
        files += glob.glob(os.path.join(REPO, ext))
        files += glob.glob(os.path.join(REPO, "jobs", "**", ext), recursive=True)
    return sorted(f for f in files if not os.path.basename(f).startswith("test_"))


# --- B3 全仓 PA 个人路径漂移守卫 ---
# 匹配 /Users/<name> 硬编码个人路径; 示例/占位名豁免 (docstring 示例 / 通用占位).
_USERS_PATH_RE = re.compile(r"/Users/([A-Za-z0-9_.-]+)")
_ILLUSTRATIVE_USERS = frozenset({"foo", "test", "you", "user", "username", "USER"})


class TestKbScriptDefaultsPortable(unittest.TestCase):
    """A1: 3 个 KB 脚本默认值必须可移植 ($HOME/.kb), 不得回退个人路径."""

    def test_default_is_home_kb(self):
        for name in _CONVERGED_KB_SCRIPTS:
            m = _KB_DEFAULT_RE.search(_read(name))
            self.assertIsNotNone(m, f"{name}: 找不到 KB_BASE/KB_DIR 默认赋值行 (格式漂移?)")
            self.assertEqual(
                m.group(1).strip(), "$HOME/.kb",
                f"{name}: KB base 默认值应为 $HOME/.kb, 实际={m.group(1)!r}",
            )

    def test_no_hardcoded_personal_path_default(self):
        for name in _CONVERGED_KB_SCRIPTS:
            self.assertNotIn(
                "KB_BASE:-/Users/bisdom", _read(name),
                f"{name}: 个人路径 /Users/bisdom 不得作为 KB base 默认值 (可移植性回退)",
            )

    def test_consistent_with_job_scripts(self):
        sample_job = os.path.join(REPO, "jobs", "dblp", "run_dblp.sh")
        if os.path.exists(sample_job):
            with open(sample_job, encoding="utf-8") as f:
                self.assertIn("${KB_BASE:-$HOME/.kb}", f.read(),
                              "参照 job 脚本应已用 $HOME/.kb — 若变则本守卫对齐前提失效")
        for name in _CONVERGED_KB_SCRIPTS:
            self.assertIn("${KB_BASE:-$HOME/.kb}", _read(name), f"{name} 未对齐 $HOME/.kb 形式")


class TestTzConfigurable(unittest.TestCase):
    """D3: 运行时 .sh 不得硬编码个人时区, 必须 config 化 (默认仍 HKT)."""

    def test_no_bare_hardcoded_tz_in_runtime_scripts(self):
        offenders = []
        for path in _runtime_shell_scripts():
            with open(path, encoding="utf-8") as f:
                if _BARE_TZ in f.read():
                    offenders.append(os.path.relpath(path, REPO))
        self.assertEqual(
            offenders, [],
            f"运行时 .sh 硬编码 {_BARE_TZ}（应为 {_CONFIG_TZ}, 见 inventory §5 D3）: {offenders}",
        )

    def test_config_form_applied_widely(self):
        # 非空验证: config 化形式确实被广泛应用 (否则 no-bare-TZ 可能是 vacuous pass).
        # 精确匹配 TZ=${SYSTEM_TZ:-Asia/Hong_Kong} 全串, 不误计仅提及 SYSTEM_TZ 的文件.
        n = 0
        for path in _runtime_shell_scripts():
            with open(path, encoding="utf-8") as f:
                if _CONFIG_TZ in f.read():
                    n += 1
        self.assertGreaterEqual(n, 30, f"config 化的 TZ 脚本数异常偏少 ({n}) — sweep 可能漏改")


class TestDiagnosePathPortable(unittest.TestCase):
    """A2: diagnose.sh 不得硬编码 /Users/bisdom 的 openclaw home."""

    def test_no_hardcoded_openclaw_home(self):
        content = _read("diagnose.sh")
        self.assertNotIn(
            "/Users/bisdom/.openclaw", content,
            "diagnose.sh: openclaw home 不得硬编码 /Users/bisdom（应为 $HOME/.openclaw）",
        )
        self.assertIn("$HOME/.openclaw/openclaw.json", content,
                      "diagnose.sh: openclaw.json 路径应用 $HOME/.openclaw")


class TestNoPersonalPathHardcode(unittest.TestCase):
    """B3 全仓漂移守卫: 任何运行时代码不得硬编码 /Users/<真实用户名> 个人路径.

    这是 B1/config 化的前瞻收尾 —— 不只守已修的 4 个文件, 而是扫全部运行时 .py+.sh,
    拦截未来任意文件新写个人绝对路径 (应改用 $HOME / expanduser("~"))。
    """

    def _scan(self):
        offenders = []
        for path in _runtime_code_files():
            with open(path, encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    for m in _USERS_PATH_RE.finditer(line):
                        if m.group(1) not in _ILLUSTRATIVE_USERS:
                            offenders.append(f"{os.path.relpath(path, REPO)}:{lineno} {m.group(0)}")
        return offenders

    def test_no_personal_users_path_in_runtime_code(self):
        offenders = self._scan()
        self.assertEqual(
            offenders, [],
            "运行时代码硬编码个人绝对路径 /Users/<name>（应用 $HOME / expanduser("
            '"~")；示例名 foo/test 已豁免）:\n  ' + "\n  ".join(offenders),
        )

    def test_scan_is_non_vacuous(self):
        # 防扫描器瘫痪 = vacuous pass: 必须真扫到足够多运行时文件.
        self.assertGreaterEqual(len(_runtime_code_files()), 80,
                                "运行时文件扫描集异常偏小 — glob 可能失效")


class TestSourceGuardMarker(unittest.TestCase):
    def test_marker(self):
        self.assertTrue(all(os.path.exists(os.path.join(REPO, n)) for n in _CONVERGED_KB_SCRIPTS))
        self.assertTrue(os.path.exists(os.path.join(REPO, "diagnose.sh")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
