#!/usr/bin/env python3
"""
test_crontab_safe_remove.py — V37.9.65 crontab_safe.sh remove command 守卫

测试新增 cmd_remove 的契约 + 安全机制 (镜像 V37.9.18 cmd_add 教训):
  1. 单 pattern 匹配单行 → 严格 count_after = count_before - 1
  2. 单 pattern 匹配多行 → 严格 count_after = count_before - matched
  3. pattern 0 匹配 → silent skip + return 0 (不修改)
  4. 拒绝全清空 (pattern 匹配所有活跃行)
  5. 严格 count 验证不一致时自动回滚
  6. backup 仍然写入 (即使 0 匹配跳过路径外, 真实 remove 必 backup)

测试用 fake-crontab shim 隔离 (PATH 注入 mock crontab) 避免动用户真 crontab.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent
SCRIPT_PATH = REPO_ROOT / "crontab_safe.sh"


def _make_fake_crontab(tmpdir, initial_lines=None):
    """构造 fake crontab shim (在 PATH 中先于真 crontab 出现).

    fake crontab 行为:
      crontab -l        → cat $tmpdir/state.txt
      crontab <file>    → cp <file> $tmpdir/state.txt (空文件视为合法清空)
      crontab -         → cat - > $tmpdir/state.txt (stdin 模式 V30 危险, 兼容)
    """
    state_file = Path(tmpdir) / "state.txt"
    if initial_lines is not None:
        state_file.write_text("\n".join(initial_lines) + ("\n" if initial_lines else ""))
    else:
        state_file.write_text("")

    bin_dir = Path(tmpdir) / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake_crontab = bin_dir / "crontab"
    fake_crontab.write_text(f"""#!/usr/bin/env bash
STATE_FILE={state_file}
case "${{1:-}}" in
    -l)
        if [ ! -s "$STATE_FILE" ]; then
            exit 1
        fi
        cat "$STATE_FILE"
        ;;
    -)
        cat - > "$STATE_FILE"
        ;;
    "")
        echo "fake crontab: missing arg" >&2
        exit 1
        ;;
    *)
        # crontab <file> install
        if [ -f "$1" ]; then
            cp "$1" "$STATE_FILE"
        else
            echo "fake crontab: file not found: $1" >&2
            exit 1
        fi
        ;;
esac
""")
    fake_crontab.chmod(0o755)
    return state_file, bin_dir


def _run_script(args, env=None, cwd=None):
    """跑 crontab_safe.sh + 给定 args. 返回 (rc, stdout, stderr, state_after)"""
    return subprocess.run(
        ["bash", str(SCRIPT_PATH)] + list(args),
        capture_output=True, text=True, env=env, cwd=cwd
    )


def _state(state_file):
    """读 fake crontab state. 返回行列表."""
    if not state_file.exists():
        return []
    text = state_file.read_text()
    return [line for line in text.split("\n") if line]


# ════════════════════════════════════════════════════════════════════
# 1. cmd_remove 基本行为
# ════════════════════════════════════════════════════════════════════
class TestRemoveBasic(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="crontab_remove_")
        self.backup_dir = Path(self.tmpdir) / "backup"
        self.backup_dir.mkdir()
        self.env = os.environ.copy()
        self.env["HOME"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _setup_crontab(self, lines):
        state_file, bin_dir = _make_fake_crontab(self.tmpdir, lines)
        self.env["PATH"] = f"{bin_dir}:{self.env.get('PATH', '')}"
        return state_file

    def test_remove_single_match_single_line(self):
        """删除唯一匹配行: count_before=3 → count_after=2"""
        state = self._setup_crontab([
            "0 8 * * * bash ~/jobs/freight_watcher/run_freight.sh",
            "0 14 * * * bash ~/jobs/freight_watcher/run_freight.sh",
            "0 20 * * * bash ~/jobs/freight_watcher/run_freight.sh",
        ])
        result = _run_script(["remove", "0 8 * * * bash ~/jobs/freight_watcher"], env=self.env)
        self.assertEqual(result.returncode, 0,
                         f"remove failed: stdout={result.stdout}, stderr={result.stderr}")
        self.assertIn("已删除 1 条", result.stdout)
        # 验证 state: 应只剩 2 行 (14:00, 20:00)
        remaining = _state(state)
        self.assertEqual(len(remaining), 2)
        self.assertNotIn("0 8 * * *", "\n".join(remaining))
        self.assertIn("0 14 * * *", "\n".join(remaining))
        self.assertIn("0 20 * * *", "\n".join(remaining))

    def test_remove_pattern_no_match_silently_skips(self):
        """pattern 0 匹配 → silent skip + 不修改 + 不 backup"""
        state = self._setup_crontab([
            "0 14 * * * bash ~/jobs/freight_watcher/run_freight.sh",
        ])
        before = _state(state)
        result = _run_script(["remove", "NONEXISTENT_PATTERN_XYZ"], env=self.env)
        self.assertEqual(result.returncode, 0)
        self.assertIn("未找到匹配", result.stdout)
        # state 完全不变
        self.assertEqual(_state(state), before)

    def test_remove_rejects_pattern_matching_all_lines(self):
        """pattern 匹配全部行 → 拒绝执行避免全清空"""
        state = self._setup_crontab([
            "0 14 * * * bash ~/A.sh",
            "0 15 * * * bash ~/B.sh",
        ])
        before = _state(state)
        # pattern "* * *" 会匹配两行 (大概率匹配所有 cron 行)
        result = _run_script(["remove", "* * *"], env=self.env)
        self.assertNotEqual(result.returncode, 0, "应拒绝全清空操作")
        self.assertIn("拒绝操作", result.stdout)
        # state 完全不变
        self.assertEqual(_state(state), before)

    def test_remove_empty_pattern_shows_usage(self):
        """空 pattern → 显示用法 + exit 1"""
        result = _run_script(["remove", ""], env=self.env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("用法", result.stdout)

    def test_remove_no_args_shows_usage(self):
        """remove 无 pattern 参数 → 用法提示"""
        result = _run_script(["remove"], env=self.env)
        self.assertNotEqual(result.returncode, 0)


# ════════════════════════════════════════════════════════════════════
# 2. 多行匹配 + 严格 count 验证
# ════════════════════════════════════════════════════════════════════
class TestRemoveMultipleMatches(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="crontab_remove_multi_")
        self.env = os.environ.copy()
        self.env["HOME"] = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _setup_crontab(self, lines):
        state_file, bin_dir = _make_fake_crontab(self.tmpdir, lines)
        self.env["PATH"] = f"{bin_dir}:{self.env.get('PATH', '')}"
        return state_file

    def test_remove_pattern_matches_multiple_lines(self):
        """单 pattern 匹配多行 → 一次性全删, count 验证 = before - matched"""
        state = self._setup_crontab([
            "0 8 * * * bash ~/jobs/freight_watcher/run_freight.sh",   # match
            "0 14 * * * bash ~/other.sh",                              # not match
            "0 20 * * * bash ~/jobs/freight_watcher/run_freight.sh",   # match
            "0 22 * * * bash ~/another.sh",                            # not match
        ])
        # pattern 匹配 freight_watcher 两行
        result = _run_script(["remove", "freight_watcher"], env=self.env)
        self.assertEqual(result.returncode, 0,
                         f"failed: stdout={result.stdout}, stderr={result.stderr}")
        self.assertIn("已删除 2 条", result.stdout)
        remaining = _state(state)
        self.assertEqual(len(remaining), 2)
        for line in remaining:
            self.assertNotIn("freight_watcher", line)


# ════════════════════════════════════════════════════════════════════
# 3. CLI / help / 接口契约
# ════════════════════════════════════════════════════════════════════
class TestCliInterface(unittest.TestCase):
    def test_help_includes_remove(self):
        """无参数 help 必须列出 remove command"""
        result = subprocess.run(
            ["bash", str(SCRIPT_PATH)], capture_output=True, text=True
        )
        self.assertIn("remove", result.stdout.lower())
        self.assertIn("V37.9.65", result.stdout)

    def test_unknown_command_shows_help(self):
        """未知 command 应显示 help (含 remove)"""
        result = subprocess.run(
            ["bash", str(SCRIPT_PATH), "unknown"], capture_output=True, text=True
        )
        self.assertIn("remove", result.stdout.lower())


# ════════════════════════════════════════════════════════════════════
# 4. 源码级守卫 (V37.9.65 marker + 安全契约)
# ════════════════════════════════════════════════════════════════════
class TestSourceLevelGuards(unittest.TestCase):
    def setUp(self):
        self.src = SCRIPT_PATH.read_text()

    def test_v37_9_65_marker_present(self):
        self.assertIn("V37.9.65", self.src,
                      "crontab_safe.sh 必须含 V37.9.65 marker (remove command 引入版本)")

    def test_cmd_remove_function_defined(self):
        # 用 (?m) multiline flag 让 ^ 匹配每行开头 (默认 re.search 只匹配字符串开头)
        self.assertRegex(self.src, r"(?m)^cmd_remove\(\)\s*\{",
                         "必须定义 cmd_remove() 函数")

    def test_remove_case_branch_present(self):
        self.assertIn('remove)', self.src)
        self.assertIn('cmd_remove "${2:-}"', self.src)

    def test_uses_grep_F_fixed_string(self):
        """必须用 grep -F (固定字符串), 不用正则避免用户传入特殊字符出错"""
        # grep -cF 计数 + grep -F 显示 + grep -vF 过滤
        self.assertIn("grep -cF", self.src)
        self.assertIn("grep -F", self.src)
        self.assertIn("grep -vF", self.src)

    def test_strict_equality_check_for_count(self):
        """严格相等验证 (V37.9.18 cmd_add 同款契约 - 防 35→35 谎报 ✅ 类血案)"""
        # cmd_remove 内必须有 -ne 严格比较
        remove_block = re.search(r"cmd_remove\(\)\s*\{(.*?)^\}", self.src,
                                 re.DOTALL | re.MULTILINE)
        self.assertIsNotNone(remove_block, "cmd_remove block not found")
        block_body = remove_block.group(1)
        self.assertIn("-ne", block_body, "必须用 -ne 严格相等比较 (防漏验证)")
        self.assertIn("expected", block_body)

    def test_reject_full_clear_protection(self):
        """必须有"拒绝全清空"保护机制 - V30 教训"""
        self.assertIn("拒绝操作", self.src)
        self.assertIn("new_active_count", self.src)

    def test_auto_rollback_on_count_mismatch(self):
        """count 不一致必须自动回滚 (cmd_restore)"""
        remove_block = re.search(r"cmd_remove\(\)\s*\{(.*?)^\}", self.src,
                                 re.DOTALL | re.MULTILINE)
        block_body = remove_block.group(1)
        self.assertIn("cmd_restore", block_body, "失败必须 cmd_restore 自动回滚")

    def test_backup_called_before_modification(self):
        """修改前必须 do_backup (与 cmd_add 同款契约)"""
        remove_block = re.search(r"cmd_remove\(\)\s*\{(.*?)^\}", self.src,
                                 re.DOTALL | re.MULTILINE)
        block_body = remove_block.group(1)
        self.assertIn("do_backup", block_body)


if __name__ == "__main__":
    unittest.main()
