#!/usr/bin/env python3
"""V37.9.324 守卫 — KB 完整性校验器「检测那一半从不运行」根治

血案（2026-08-21 对抗审计，探针实证）:
  kb_integrity.py 的生产唯一调用是 openclaw_backup.sh 里的 `--update`（每日 03:00
  备份后刷新基线）。verify() 从未在生产被调用过 —— 检测那一半结构上不可达，而每日
  重设基线会把灾难静默吸收:
      Day1 --update  → baseline notes=2627
      Day2 notes/ 被清空 (2627 → 0)
      Day2 --update  → baseline notes=0  ← 灾难进基线
      Day3 verify    → exit 0 / status "ok" / alerts []  ← 2627 篇笔记消失, 报无异常
  而 7 个既有单测全绿, 因为它们断言的是「源码里有『已消失』字样」而不是「这段代码
  会被执行」(V37.9.320 SS-1 家族: 静态 grep 给分, 运行时从不校验)。

修复三件:
  (1) openclaw_backup.sh 在 --update **之前**先跑 verify, rc≠0 打一行 ERROR:
      让 openclaw_backup.log 的既有 watchdog 错误扫描接住（零新机器）。
  (2) load_checksums 区分「不存在」与「读不动」—— 此前指纹库被写坏会报成
      「指纹库不存在，请先 --init」= 把扫描器自身失效说成从未初始化
      (V37.9.320 AL-2 同族: 跑不动 ≠ 没问题)。
  (3) scan_permissions 的 status.json 分支去 TOCTOU —— 此前 isfile 与 stat 之间
      文件消失会抛 FileNotFoundError, 而调用方 `|| true` 会把它吞成静默。
"""
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import contextlib

REPO = os.path.dirname(os.path.abspath(__file__))
BACKUP_SH = os.path.join(REPO, "openclaw_backup.sh")
INTEGRITY_PY = os.path.join(REPO, "kb_integrity.py")
WATCHDOG_SH = os.path.join(REPO, "job_watchdog.sh")


def _load_integrity():
    spec = importlib.util.spec_from_file_location("kbi_under_test", INTEGRITY_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _strip_comments(text):
    """只留可执行行 —— 源码断言绝不能被自己的解释性注释满足 (V37.9.178 家族)。"""
    return "\n".join(l for l in text.splitlines() if not l.strip().startswith("#"))


def _integrity_block():
    """从 openclaw_backup.sh 真源码切出 KB 完整性块（注释头 → 配对的 fi）。"""
    src = _read(BACKUP_SH)
    # 🔴 必须锚到列 0 的外层 fi —— 块内还有一个缩进 4 空格的 `if [ "$KBI_RC" ...]`,
    # 非贪婪匹配到 `\n\s*fi` 会停在内层 fi, 把 --update 那行切掉（首版守卫即被此坑
    # 咬出两个假失败: 「块内没有 --update 调用」）。
    m = re.search(r'if \[ -f "\$HOME/kb_integrity\.py" \];.*?\nfi\n', src, re.S)
    assert m, "openclaw_backup.sh 里找不到 kb_integrity 调用块"
    return m.group(0)


def _watchdog_err_pattern():
    """从 job_watchdog.sh 抽真实 err_pattern（不 hardcode, 防守卫-实现漂移）。"""
    src = _read(WATCHDOG_SH)
    m = re.search(r"local err_pattern='([^']+)'", src)
    assert m, "job_watchdog.sh 里找不到 err_pattern"
    return m.group(1)


def _bre_to_python(pattern):
    """watchdog 用 grep -E, 本模式在 Python re 下语义等价, 直接编译。"""
    return re.compile(pattern, re.IGNORECASE)


class _KbFixture:
    """真 kb_integrity 模块 + 隔离的临时 KB 目录（绝不碰 ~/.kb, MR-9）。"""

    def __init__(self, notes=20):
        self.mod = _load_integrity()
        self.tmp = tempfile.mkdtemp()
        m = self.mod
        m.KB_DIR = self.tmp
        m.INTEGRITY_DIR = os.path.join(self.tmp, ".integrity")
        m.CHECKSUMS_FILE = os.path.join(m.INTEGRITY_DIR, "checksums.json")
        os.chmod(self.tmp, 0o700)
        for d in ("notes", "sources", "text_index", "mm_index"):
            os.makedirs(os.path.join(self.tmp, d))
        for i in range(notes):
            with open(os.path.join(self.tmp, "notes", f"n{i}.md"), "w") as f:
                f.write("real knowledge")
        self._write_status()
        for name, body in (("index.json", '{"a":1}'), ("daily_digest.md", "digest")):
            p = os.path.join(self.tmp, name)
            with open(p, "w") as f:
                f.write(body)
            os.chmod(p, 0o600)

    def _write_status(self, focus="f"):
        p = os.path.join(self.tmp, "status.json")
        with open(p, "w") as f:
            json.dump({"priorities": [], "recent_changes": [], "feedback": [],
                       "health": {}, "focus": focus}, f)
        os.chmod(p, 0o600)

    def update(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.mod.init_or_update()
        return buf.getvalue()

    def verify_json(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = self.mod.verify(json_output=True)
        return rc, json.loads(buf.getvalue())

    def verify_text(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = self.mod.verify(json_output=False)
        return rc, buf.getvalue()

    def wipe_notes(self):
        shutil.rmtree(os.path.join(self.tmp, "notes"))
        os.makedirs(os.path.join(self.tmp, "notes"))

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestVerifyReachableInProduction(unittest.TestCase):
    """核心: 检测那一半必须真的会在生产被执行。"""

    def test_blood_case_wiped_notes_detected_by_verify(self):
        """行为级血案回归: notes/ 清空 → verify 必须 rc=1 且报「已清空」。"""
        fx = _KbFixture(notes=2627)
        try:
            fx.update()                      # Day1 基线
            fx.wipe_notes()                  # Day2 灾难
            rc, d = fx.verify_json()         # Day2 —— 修复后先跑的这一步
            self.assertEqual(rc, 1, "清空 notes/ 必须非零退出")
            self.assertEqual(d["status"], "alert")
            self.assertTrue(any("已清空" in a for a in d["alerts"]),
                            f"alerts 未报清空: {d['alerts']}")
        finally:
            fx.close()

    def test_blood_case_update_only_absorbs_disaster(self):
        """反向证据: 只跑 --update（修复前的生产形态）会把灾难吸收进基线。

        这条不是断言 bug 还在, 而是钉死「为什么顺序 load-bearing」——
        --update 先跑, 灾难就永远查不出来了。
        """
        fx = _KbFixture(notes=2627)
        try:
            fx.update()
            fx.wipe_notes()
            fx.update()                      # 生产旧形态: 直接重设基线
            rc, d = fx.verify_json()
            self.assertEqual(rc, 0, "基线被重设后 verify 结构上抓不到 —— 这正是血案")
            self.assertEqual(d["alerts"], [])
        finally:
            fx.close()

    def test_backup_calls_verify_before_update(self):
        """源码: openclaw_backup.sh 必须先 verify 再 --update（顺序 load-bearing）。"""
        block = _strip_comments(_integrity_block())
        calls = [l for l in block.splitlines()
                 if "kb_integrity.py" in l and "python3" in l]
        self.assertGreaterEqual(len(calls), 2,
                                f"块内 kb_integrity 调用少于 2 次: {calls}")
        verify_idx = next((i for i, l in enumerate(calls) if "--update" not in l), None)
        update_idx = next((i for i, l in enumerate(calls) if "--update" in l), None)
        self.assertIsNotNone(verify_idx, "块内没有不带 --update 的 verify 调用")
        self.assertIsNotNone(update_idx, "块内没有 --update 调用")
        self.assertLess(verify_idx, update_idx,
                        "verify 必须在 --update 之前 —— 否则基线已重设, 什么都查不到")

    def test_verify_exit_code_is_captured_not_swallowed(self):
        """源码: verify 的 rc 必须被捕获, 不能像 --update 那样 `|| true` 吞掉。"""
        block = _strip_comments(_integrity_block())
        verify_line = next(l for l in block.splitlines()
                           if "kb_integrity.py" in l and "python3" in l
                           and "--update" not in l)
        self.assertNotIn("|| true", verify_line,
                         "verify 的退出码被 `|| true` 吞掉 = 告警永不触发")
        self.assertRegex(verify_line, r"\|\|\s*\w+=\$\?",
                         "verify 必须用 `|| VAR=$?` 捕获 rc (set -e 安全)")

    def test_nonzero_rc_emits_alert_line(self):
        """源码: rc≠0 时必须真的打出一行告警, 不是只记 rc。"""
        block = _strip_comments(_integrity_block())
        self.assertRegex(block, r'-ne 0 \]', "缺少 rc≠0 分支")
        self.assertRegex(block, r'echo .*ERROR:.*>> "\$LOG"',
                         "rc≠0 分支未向 $LOG 打 ERROR 行")


class TestWatchdogErrPatternContract(unittest.TestCase):
    """MR-8 跨文件契约: 告警行必须被 watchdog 接住, 正常输出必须不误报。"""

    def test_alert_line_matches_real_watchdog_pattern(self):
        pat = _bre_to_python(_watchdog_err_pattern())
        block = _strip_comments(_integrity_block())
        echo_lines = [l for l in block.splitlines() if "ERROR" in l and "echo" in l]
        self.assertTrue(echo_lines, "块内没有 ERROR echo 行")
        for line in echo_lines:
            self.assertTrue(pat.search(line),
                            f"告警行不匹配 watchdog err_pattern, 等于没人扫: {line}")

    def test_verify_normal_output_never_matches_err_pattern(self):
        """行为级: 真 verify 的正常输出不得匹配 err_pattern（否则每日误报）。"""
        pat = _bre_to_python(_watchdog_err_pattern())
        fx = _KbFixture()
        try:
            fx.update()
            fx._write_status(focus="changed daily")   # 正常的每日内容变更
            rc, text = fx.verify_text()
            self.assertEqual(rc, 0, "正常每日变更不应告警")
            hits = [l for l in text.splitlines() if pat.search(l)]
            self.assertEqual(hits, [], f"正常输出误撞 err_pattern: {hits}")
        finally:
            fx.close()

    def test_alert_detail_lines_do_not_match_err_pattern(self):
        """行为级: 告警明细行刻意不匹配 —— 可见性只靠 shell 那行显式 ERROR。

        （镜像 V37.9.292 契约: per-file ❌/⚠️ 不匹配, run 级 ERROR: 匹配。）
        """
        pat = _bre_to_python(_watchdog_err_pattern())
        fx = _KbFixture(notes=30)
        try:
            fx.update()
            fx.wipe_notes()
            rc, text = fx.verify_text()
            self.assertEqual(rc, 1)
            self.assertIn("已清空", text)
            hits = [l for l in text.splitlines() if pat.search(l)]
            self.assertEqual(hits, [], f"明细行撞 err_pattern: {hits}")
        finally:
            fx.close()

    def test_backup_log_is_scanned_by_watchdog(self):
        """防空转: openclaw_backup.log 必须在 HOME_LOGS 里, 否则 ERROR 行无人扫。"""
        src = _read(WATCHDOG_SH)
        m = re.search(r"HOME_LOGS=\((.*?)\n\)", src, re.S)
        self.assertIsNotNone(m, "job_watchdog.sh 里找不到 HOME_LOGS")
        self.assertIn("openclaw_backup.log", m.group(1))


class TestBaselineHonesty(unittest.TestCase):
    """指纹库读不动 ≠ 从未初始化 (V37.9.320 AL-2 家族)。"""

    def test_corrupt_baseline_not_reported_as_missing(self):
        fx = _KbFixture()
        try:
            fx.update()
            with open(fx.mod.CHECKSUMS_FILE, "w") as f:
                f.write("{ this is not json")
            rc, d = fx.verify_json()
            self.assertEqual(rc, 1)
            self.assertEqual(d["status"], "corrupt_baseline",
                             "指纹库损坏被报成别的状态")
            self.assertNotIn("不存在", d.get("message", ""),
                             "损坏不得措辞为「不存在」")
        finally:
            fx.close()

    def test_missing_baseline_still_no_baseline(self):
        fx = _KbFixture()
        try:
            rc, d = fx.verify_json()
            self.assertEqual(rc, 1)
            self.assertEqual(d["status"], "no_baseline")
        finally:
            fx.close()

    def test_init_message_distinguishes_first_run(self):
        fx = _KbFixture()
        try:
            first = fx.update()
            second = fx.update()
            self.assertIn("初始化", first, f"首次应说初始化: {first!r}")
            self.assertIn("更新", second, f"第二次应说更新: {second!r}")
        finally:
            fx.close()


class TestPermissionScanRobustness(unittest.TestCase):
    def test_stat_failure_is_reported_not_raised(self):
        """权限检查器不得在它要报告的那种权限损坏上崩掉。

        真实可达路径: ~/.kb 权限被改坏（正是本函数要报的东西）时, 对子文件 stat
        会抛 PermissionError —— 旧写法裸 stat 让它逃出 verify, 而调用方 `|| true`
        会把它吞成静默 = 正在治的病。

        🔴 诚实边界: 用 monkeypatch 制造 stat 失败, 而不是 chmod ——
        本进程可能以 root 跑, root 无视权限位, chmod 探针会得到假阴性
        (V37.9.320 实录: root 下 chmod 0o500 的探针曾给出错误结论)。
        另: 首版守卫写的是「删掉 status.json 不得崩」, 但 os.path.isfile 会正确
        返回 False 根本不走 stat —— 那条断言是空转的, S4 sabotage 当场没开火才
        暴露出来 (V37.9.293 S3 同款: sabotage 流程抓出守卫自身的弱点)。
        """
        fx = _KbFixture()
        real_stat = os.stat
        target = os.path.join(fx.tmp, "status.json")

        def fake_stat(path, *a, **kw):
            if str(path) == target:
                raise PermissionError(13, "Permission denied")
            return real_stat(path, *a, **kw)

        os.stat = fake_stat
        try:
            issues = fx.mod.scan_permissions()   # 不抛即通过
            self.assertIsInstance(issues, list)
            self.assertTrue(any("status.json" in i for i in issues),
                            f"stat 失败必须被报告而非静默: {issues}")
        finally:
            os.stat = real_stat
            fx.close()

    def test_missing_status_json_is_not_a_permission_issue(self):
        """回归: 文件不存在由 checksums 分支负责报告, 权限扫描不重复报。"""
        fx = _KbFixture()
        try:
            os.remove(os.path.join(fx.tmp, "status.json"))
            self.assertEqual(fx.mod.scan_permissions(), [])
        finally:
            fx.close()

    def test_world_readable_dir_flagged_with_remedy(self):
        fx = _KbFixture()
        try:
            os.chmod(fx.tmp, 0o755)
            issues = fx.mod.scan_permissions()
            self.assertTrue(issues, "755 必须被标记")
            self.assertTrue(any("chmod" in i for i in issues),
                            f"告警必须带可执行的修复指引: {issues}")
        finally:
            os.chmod(fx.tmp, 0o700)
            fx.close()

    def test_secure_perms_produce_no_issue(self):
        """防误报: 700 + 600 必须干净。"""
        fx = _KbFixture()
        try:
            self.assertEqual(fx.mod.scan_permissions(), [])
        finally:
            fx.close()


class TestSourceGuards(unittest.TestCase):
    def test_markers_present(self):
        self.assertIn("V37.9.324", _read(BACKUP_SH))
        self.assertIn("V37.9.324", _read(INTEGRITY_PY))

    def test_bash_syntax_ok(self):
        r = subprocess.run(["bash", "-n", BACKUP_SH], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_block_extraction_non_vacuous(self):
        """防空转: 切片必须真拿到内容, 否则上面所有源码断言形同虚设。"""
        block = _integrity_block()
        self.assertGreater(len(block.splitlines()), 4)
        self.assertIn("kb_integrity.py", block)
        # 切片必须覆盖到外层 fi —— 否则 verify/--update 顺序断言会在半截块上假通过
        self.assertIn("--update", block, "切片没覆盖到 --update 行（切在内层 fi 了）")
        self.assertIn("KBI_RC", block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
