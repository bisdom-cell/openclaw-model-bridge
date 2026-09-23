#!/usr/bin/env python3
"""test_v37_9_358_atomic_deploy_copy.py — V37.9.358 auto_deploy 原子复制守卫

血案 2026-09-22 11:12:07 (Mac Mini auto_deploy.log 实录):
  V37.9.355 改了 jobs/semantic_scholar/run_semantic_scholar.sh, auto_deploy 在 11:00 那轮
  S2 仍在 429 退避循环里时用就地 cp 覆盖了同一个 inode. bash 是边读边执行脚本文件的:
  内存里的旧 for 循环(16 个关键词)跑完后, 从被改写(多了两个关键词=偏移错位)的文件读
  下一条命令 → "line 153: syntax error near unexpected token `)'" → 状态文件没写成,
  次日 watchdog 才因 last_run 陈旧告警.
修复: deploy_copy() = 同目录临时文件 + mv -f (rename(2) 换目录项, 运行中进程持有旧 inode).

本守卫:
  1. 行为级 — 从 auto_deploy.sh 真源码抽 deploy_copy 跑真 bash:
     旧 fd 仍读旧内容 + inode 变化 / 反向证据 plain cp 同 inode 旧 fd 读到新内容 /
     🔴 血案回归: 运行中的 bash 脚本被 deploy_copy 替换仍输出旧尾行, 被就地 cp 替换则
     执行新文件内容(证机制真实非推测) / 失败清理临时文件 / 执行位保留 / 无残留.
  2. 源码守卫 — 三个复制站点全部经 deploy_copy, 裸 cp 形态退役 (剥注释行),
     helper 用同目录 tmp + mv -f, V37.8.12 自复制守卫保留, helper 先于首次调用, bash -n.
"""
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
AUTO_DEPLOY = os.path.join(REPO, "auto_deploy.sh")
CALL = 'deploy_copy "$REPO_DIR/$SRC" "$DST"'
BARE_CP = 'cp "$REPO_DIR/$SRC" "$DST"'


def _read():
    with open(AUTO_DEPLOY, encoding="utf-8") as f:
        return f.read()


def _executable_lines(text):
    return [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]


def _extract_helper(text):
    m = re.search(r"^deploy_copy\(\) \{\n.*?^\}\n", text, re.M | re.S)
    if not m:
        raise AssertionError("auto_deploy.sh 里找不到 deploy_copy() 定义")
    return m.group(0)


def _run_helper(helper_src, src, dst):
    return subprocess.run(
        ["bash", "-c", helper_src + '\ndeploy_copy "$1" "$2"', "_", src, dst],
        capture_output=True, text=True, timeout=30,
    )


def _write_padded_script(path, tail_marker, sleep_s=3):
    pad = "".join(f"# pad {i:06d} {'.' * 52}\n" for i in range(1200))
    with open(path, "w") as f:
        f.write(f"sleep {sleep_s}\n{pad}echo {tail_marker}\n")


class TestDeployCopyHelperBehavior(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = _extract_helper(_read())

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v358_")
        self.src = os.path.join(self.tmp, "src.sh")
        self.dst = os.path.join(self.tmp, "deploy", "dst.sh")
        os.makedirs(os.path.dirname(self.dst))

    def _seed(self, old="OLD\n", new="NEW\n"):
        with open(self.dst, "w") as f:
            f.write(old)
        with open(self.src, "w") as f:
            f.write(new)

    def test_dst_gets_new_content_old_fd_keeps_old_inode(self):
        self._seed()
        fd = os.open(self.dst, os.O_RDONLY)
        try:
            ino_before = os.fstat(fd).st_ino
            r = _run_helper(self.helper, self.src, self.dst)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(self.dst) as f:
                self.assertEqual(f.read(), "NEW\n")
            self.assertNotEqual(os.stat(self.dst).st_ino, ino_before, "mv 必须换 inode")
            os.lseek(fd, 0, os.SEEK_SET)
            self.assertEqual(os.read(fd, 100), b"OLD\n", "旧 fd 必须仍读到旧内容")
        finally:
            os.close(fd)

    def test_reverse_evidence_plain_cp_rewrites_same_inode(self):
        """证明 helper 的 inode 语义是 load-bearing: 裸 cp 让旧 fd 读到新内容."""
        self._seed()
        fd = os.open(self.dst, os.O_RDONLY)
        try:
            ino_before = os.fstat(fd).st_ino
            subprocess.run(["cp", self.src, self.dst], check=True)
            self.assertEqual(os.stat(self.dst).st_ino, ino_before)
            os.lseek(fd, 0, os.SEEK_SET)
            self.assertEqual(os.read(fd, 100), b"NEW\n", "就地 cp 下旧 fd 读到的是新内容 = 血案机制")
        finally:
            os.close(fd)

    def _start_running_script(self, path, marker):
        _write_padded_script(path, marker)
        proc = subprocess.Popen(["bash", path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        time.sleep(1.0)
        return proc

    def test_blood_case_running_script_survives_deploy_copy(self):
        """运行中的脚本被 deploy_copy 替换 → 仍执行旧内容 (OLD_TAIL)."""
        _write_padded_script(self.src, "NEW_TAIL")
        proc = self._start_running_script(self.dst, "OLD_TAIL")
        r = _run_helper(self.helper, self.src, self.dst)
        self.assertEqual(r.returncode, 0, r.stderr)
        out, _ = proc.communicate(timeout=20)
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("OLD_TAIL", out)
        self.assertNotIn("NEW_TAIL", out)

    def test_reverse_evidence_running_script_corrupted_by_in_place_cp(self):
        """同一脚本被就地 cp 替换 → bash 从被改写文件读到新尾行 (血案机制真实可复现)."""
        _write_padded_script(self.src, "NEW_TAIL")
        proc = self._start_running_script(self.dst, "OLD_TAIL")
        subprocess.run(["cp", self.src, self.dst], check=True)
        out, _ = proc.communicate(timeout=20)
        self.assertIn("NEW_TAIL", out, "就地 cp 必须让运行中的 bash 读到新内容, 否则本测试前提不成立")
        self.assertNotIn("OLD_TAIL", out)

    def test_exec_bit_preserved_and_no_temp_left(self):
        self._seed()
        os.chmod(self.src, 0o755)
        r = _run_helper(self.helper, self.src, self.dst)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.stat(self.dst).st_mode & stat.S_IXUSR, "源文件执行位必须带到目标")
        leftovers = [n for n in os.listdir(os.path.dirname(self.dst)) if ".deploy." in n]
        self.assertEqual(leftovers, [], "不得残留 .deploy.<pid> 临时文件")

    def test_failure_cleans_temp_and_leaves_dst_untouched(self):
        with open(self.dst, "w") as f:
            f.write("OLD\n")
        r = _run_helper(self.helper, os.path.join(self.tmp, "missing.sh"), self.dst)
        self.assertNotEqual(r.returncode, 0)
        with open(self.dst) as f:
            self.assertEqual(f.read(), "OLD\n")
        leftovers = [n for n in os.listdir(os.path.dirname(self.dst)) if ".deploy." in n]
        self.assertEqual(leftovers, [])


class TestAutoDeploySourceGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _read()
        cls.exe = "\n".join(_executable_lines(cls.text))

    def test_helper_defined_once_with_same_dir_tmp_and_mv(self):
        self.assertEqual(self.text.count("deploy_copy() {"), 1)
        helper = _extract_helper(self.text)
        self.assertIn('_tmp="${_dst}.deploy.$$"', helper, "临时文件必须与目标同目录 (跨目录 mv 不是 rename)")
        self.assertRegex(helper, r'mv -f "\$_tmp" "\$_dst"')
        self.assertRegex(helper, r'rm -f "\$_tmp"', "失败路径必须清理临时文件")

    def test_bare_cp_form_retired_all_sites_use_helper(self):
        self.assertNotIn(BARE_CP, self.exe, "裸 cp 形态必须退役 (可执行行)")
        self.assertEqual(self.exe.count(CALL), 3, "主同步 + 漂移修复(缺失) + 漂移修复(不一致) 三站点")
        for tag in ("同步:", "漂移修复(缺失):", "漂移修复(不一致):"):
            self.assertIn(tag, self.text)

    def test_helper_defined_before_first_call(self):
        lines = self.text.splitlines()
        def_line = next(i for i, ln in enumerate(lines) if ln.startswith("deploy_copy() {"))
        first_call = next(i for i, ln in enumerate(lines) if CALL in ln)
        self.assertLess(def_line, first_call)

    def test_self_copy_guard_v37_8_12_preserved(self):
        self.assertGreaterEqual(self.exe.count('if [ "$REPO_DIR/$SRC" = "$DST" ]'), 2)

    def test_marker_and_syntax(self):
        self.assertIn("V37.9.358", self.text)
        r = subprocess.run(["bash", "-n", AUTO_DEPLOY], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
