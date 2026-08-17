#!/usr/bin/env python3
"""V37.9.314 (2026-08-17) — 2026-08-14 审计余项六连收口 (b/d/e/f/g/h)。

用户指令「全部开始执行」: unfinished [43] 的 6 个未修 sub-finding 一次清扫。

  (h) openclaw_backup: 保留策略加 MIN_KEEP 下限 + 产物校验 verify_archive
      —— 8-15~17 中断事件实证的两个洞: 中断 8 天恢复当晚会删光旧档只剩 1 份
      (中断越久剩越少, 恰好反了); create rc=0 但 0 字节/截断档照样计入备份集。
  (g) crontab_safe: 写操作 (add/remove/restore) mkdir 互斥锁 —— RMW 无锁时并发
      写者互相覆盖静默丢行, 正是本工具要防的事故类。
  (b) upgrade_openclaw: 裸 `$OPENCLAW gateway &` 违 V37.9.13 单一管理者 (与
      launchd KeepAlive 抢 :18789 = V37.9.12.1 双管理血案同型) → 收编 restart.sh;
      HEALTH 变量此前只展示从不参与成功判定。
  (f) TZ python-side 家族 (A-F8): kb_dream_helpers (14 天 ban-list 日历窗) 与
      cross_source_signal_aggregator (daily_signals_{date} 文件名) 的
      datetime.now() 曾随系统 TZ 漂移 → 与 shell 侧 SYSTEM_TZ pin (V37.9.250 D3)
      对齐; kb_write.sh TS 行补 D3 漏网。
  (d) kb_dream REDUCE_DATA (A-F6): 真正进 LLM 的是 30K 截断 (保头截尾), 而 Radar
      三件套在尾部 → 素材几乎每晚 >30K, 三件套从未进过 LLM = 雷达管道对 dream
      结构性哑炮。修: 小块前插 preamble; 退役 80K REDUCE_PROMPT/REDUCE_SYSTEM
      死代码 (~114 行, V37.9.68 三阶改造后 llm_call 无消费); REDUCE_CHARS 改记
      真实发送量 (原虚标 80K 死路径字节数进 dream 产物/last_run 元数据)。
  (e) top_alignment_picker (A-F7): LLM 字段乱序 (🎚️ 在 ⭐ 前) 时 "⭐ 评级:" 字段
      头被吸进 alignment 文本, _extract_stars 取全文最长 ⭐ 段 → 评级 5 星覆盖
      对齐 3 星, 低对齐条目冒充高对齐进 Top 5。修: 带 评级/评分 标签的 ⭐ 行是
      字段头 (无论当前字段) + 星数只从 alignment 首个含 ⭐ 的行抽取 (双层)。
"""

import os
import re
import subprocess
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))


def _read(name):
    with open(os.path.join(REPO, name), encoding="utf-8") as fh:
        return fh.read()


def _extract_fn(src, name):
    i = src.index(f"{name}() {{")
    j = src.index("\n}", i) + 2
    return src[i:j]


# ── (h) openclaw_backup ──────────────────────────────────────────────────


class TestBackupRetentionFloor(unittest.TestCase):
    """MIN_KEEP 下限: 无论多旧, 最新 N 份永不删。"""

    def setUp(self):
        self.src = _read("openclaw_backup.sh")

    def test_min_keep_defined(self):
        self.assertRegex(self.src, r"(?m)^MIN_KEEP=3$")

    def test_prune_uses_helper_not_bare_find_delete(self):
        self.assertIn('DELETED=$(prune_old_backups "$BACKUP_DIR")', self.src)
        self.assertNotRegex(
            self.src,
            r'find "\$BACKUP_DIR" -name "openclaw-backup-\*\.tar\.gz" -mtime[^\n]*-delete',
            "退役: 裸 find -delete (无最少份数保护)",
        )

    def test_prune_behavior_interruption_scenario(self):
        """🔴 血案场景: 中断后全部档超龄 → 最新 3 份仍保留。"""
        import shutil, tempfile
        tmp = tempfile.mkdtemp(prefix="v379314_prune_")
        self.addCleanup(shutil.rmtree, tmp, True)
        d = os.path.join(tmp, "dir")
        os.makedirs(d)
        for i in range(1, 6):  # 5 份, 全部 10+ 天前
            p = os.path.join(d, f"openclaw-backup-2026-08-0{i}.tar.gz")
            with open(p, "w") as fh:
                fh.write("x")
            age = 86400 * (9 + i)
            t = __import__("time").time() - age
            os.utime(p, (t, t))
        fns = _extract_fn(self.src, "prune_old_backups")
        r = subprocess.run(
            ["bash", "-c", f'KEEP_DAYS=7; MIN_KEEP=3\n{fns}\nprune_old_backups "{d}"'],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(r.stdout.strip(), "2", f"应删 2 留 3: {r.stdout} {r.stderr}")
        remaining = [f for f in os.listdir(d) if f.endswith(".tar.gz")]
        self.assertEqual(len(remaining), 3, "最新 3 份必须存活 (MIN_KEEP 下限)")


class TestBackupArchiveVerification(unittest.TestCase):
    """产物校验: rc=0 ≠ 产物完好。"""

    def setUp(self):
        self.src = _read("openclaw_backup.sh")

    def test_both_success_paths_verify(self):
        """主路径与 fallback 路径都必须在打 OK 前 verify + exit 4。"""
        self.assertEqual(
            self.src.count('if ! verify_archive "$BACKUP_FILE"; then'), 2,
            "主路径 + fallback 各一次校验",
        )
        # 锚定可执行行 (注释里也写着 "exit 4" —— V37.9.178/301/308 同款注释陷阱)
        self.assertEqual(
            len(re.findall(r"(?m)^\s+exit 4$", self.src)), 2,
            "两条校验失败路径各一个 exit 4",
        )

    def test_verify_behavior(self):
        """好档过 / 太小拒 / 尺寸够但结构坏拒。"""
        import shutil, tempfile
        tmp = tempfile.mkdtemp(prefix="v379314_vfy_")
        self.addCleanup(shutil.rmtree, tmp, True)
        payload = os.path.join(tmp, "payload")
        os.makedirs(payload)
        with open(os.path.join(payload, "f"), "wb") as fh:
            fh.write(os.urandom(2_000_000))
        good = os.path.join(tmp, "good.tar.gz")
        subprocess.run(["tar", "czf", good, "-C", tmp, "payload"], check=True)
        small = os.path.join(tmp, "small.tar.gz")
        with open(small, "wb") as fh:
            fh.write(os.urandom(100))
        corrupt = os.path.join(tmp, "corrupt.tar.gz")
        with open(corrupt, "wb") as fh:
            fh.write(os.urandom(3_000_000))
        fns = _extract_fn(self.src, "verify_archive")
        def run(f):
            return subprocess.run(
                ["bash", "-c",
                 f'MIN_ARCHIVE_BYTES=1048576; LOG=/dev/null; TIMESTAMP=t\n{fns}\nverify_archive "{f}"'],
                capture_output=True, timeout=60,
            ).returncode
        self.assertEqual(run(good), 0, "完好档必须通过")
        self.assertNotEqual(run(small), 0, "过小档必须拒绝")
        self.assertNotEqual(run(corrupt), 0, "结构损坏档必须拒绝")

    def test_verify_errors_match_watchdog_pattern(self):
        wd = _read("job_watchdog.sh")
        pat = re.compile(re.search(r"local err_pattern='([^']+)'", wd).group(1))
        errs = [ln for ln in self.src.splitlines() if "verify failed" in ln]
        self.assertTrue(errs)
        for ln in errs:
            self.assertTrue(pat.search(ln), f"verify ERROR 行不匹配 watchdog: {ln[:60]}")


# ── (g) crontab_safe ─────────────────────────────────────────────────────


class TestCrontabSafeLock(unittest.TestCase):
    def setUp(self):
        self.src = _read("crontab_safe.sh")

    def test_write_ops_acquire_lock(self):
        """add / remove / restore 三个写操作分支必须先拿锁。"""
        m = re.search(r'case "\$\{1:-help\}" in(.*?)esac', self.src, re.DOTALL)
        self.assertIsNotNone(m)
        case_block = m.group(1)
        for op in ("add", "remove", "restore"):
            branch = re.search(rf"{op}\)\s*\n(.*?);;", case_block, re.DOTALL)
            self.assertIsNotNone(branch, f"{op} 分支未找到")
            self.assertIn("acquire_lock", branch.group(1), f"{op} 是 RMW 写操作, 必须拿锁")

    def test_read_ops_do_not_lock(self):
        """backup / verify 是只读操作, 不得因锁被并发写操作阻塞。"""
        m = re.search(r'case "\$\{1:-help\}" in(.*?)esac', self.src, re.DOTALL)
        case_block = m.group(1)
        for op in ("backup", "verify"):
            branch = re.search(rf"{op}\)\s*\n(.*?);;", case_block, re.DOTALL)
            self.assertIsNotNone(branch)
            self.assertNotIn("acquire_lock", branch.group(1), f"{op} 只读, 不应加锁")

    def test_lock_is_mkdir_atomic_with_stale_recovery(self):
        self.assertIn('mkdir "$LOCKDIR"', self.src)
        self.assertIn("-mmin +2", self.src, "stale 锁 (>120s) 必须可回收, 防持有者被 kill 后永久死锁")
        self.assertRegex(self.src, r"trap 'rmdir \"\$LOCKDIR\"", "退出必须释放锁")

    def test_lock_timeout_aborts_not_proceeds(self):
        """拿不到锁必须 abort —— 无锁继续 = 放弃本工具的存在意义。"""
        self.assertIn("未拿到锁", self.src)
        block = self.src[self.src.index("未拿到锁"):]
        self.assertIn("exit 1", block[:200])


# ── (b) upgrade_openclaw ─────────────────────────────────────────────────


class TestUpgradeSingleManager(unittest.TestCase):
    def setUp(self):
        self.src = _read("upgrade_openclaw.sh")

    def test_bare_background_gateway_retired(self):
        """反模式: 裸 `$OPENCLAW gateway ... &` 与 launchd KeepAlive 双管理。"""
        self.assertNotRegex(
            self.src, r"(?m)^\s*\$OPENCLAW gateway[^\n]*&\s*$",
            "退役: 手动后台 Gateway (V37.9.12.1 双管理血案同型)",
        )

    def test_uses_restart_sh(self):
        self.assertIn('bash "$HOME/restart.sh"', self.src)

    def test_health_participates_in_verdict(self):
        """HEALTH 此前只展示从不判定 —— 现在必须参与成功条件。"""
        self.assertRegex(
            self.src,
            r'if \[ "\$GW" = "UP" \] && \[ "\$HEALTH" != "无响应" \]',
            "升级成功判定必须同时要求 Gateway UP 且 proxy /health 有响应",
        )


# ── (f) TZ python-side ───────────────────────────────────────────────────


class TestTzPythonSide(unittest.TestCase):
    def test_modules_pin_tz(self):
        for mod in ("kb_dream_helpers.py", "cross_source_signal_aggregator.py"):
            with self.subTest(module=mod):
                src = _read(mod)
                self.assertIn('os.environ["TZ"] = os.environ.get("SYSTEM_TZ", "Asia/Hong_Kong")', src)
                self.assertIn("tzset()", src)

    def test_kb_write_ts_pinned(self):
        self.assertIn("TS=$(TZ=${SYSTEM_TZ:-Asia/Hong_Kong} date", _read("kb_write.sh"))

    def test_behavior_utc_system_becomes_hkt(self):
        """行为级: 系统 TZ=UTC 时 import 后进程时区应为 HKT (+8)。"""
        env = dict(os.environ)
        env["TZ"] = "UTC"
        env.pop("SYSTEM_TZ", None)
        r = subprocess.run(
            ["python3", "-c",
             "import kb_dream_helpers, time; print(int(-time.timezone/3600))"],
            capture_output=True, text=True, env=env, cwd=REPO, timeout=60,
        )
        self.assertEqual(r.stdout.strip(), "8", f"TZ pin 未生效: {r.stdout} {r.stderr}")

    def test_behavior_system_tz_overrides(self):
        env = dict(os.environ)
        env["TZ"] = "UTC"
        env["SYSTEM_TZ"] = "America/New_York"
        r = subprocess.run(
            ["python3", "-c",
             "import kb_dream_helpers, time; print(time.tzname[0])"],
            capture_output=True, text=True, env=env, cwd=REPO, timeout=60,
        )
        self.assertIn(r.stdout.strip(), ("EST", "EDT"), "SYSTEM_TZ env 必须可覆盖默认 HKT")


# ── (d) kb_dream REDUCE_DATA ─────────────────────────────────────────────


class TestDreamRadarPreamble(unittest.TestCase):
    def setUp(self):
        self.src = _read("kb_dream.sh")

    def test_radar_blocks_go_to_preamble(self):
        """三件套 + 状态/趋势必须注入 REDUCE_PREAMBLE (前插), 不再尾部追加。"""
        for marker in ("Opportunity Radar #1", "Opportunity Radar #2", "Opportunity Radar #3"):
            i = self.src.index(marker)
            j = self.src.rfind("+=\"", 0, i)
            assign = self.src[self.src.rfind("\n", 0, j) + 1 : j + 3]
            self.assertIn(
                "REDUCE_PREAMBLE", assign,
                f"{marker} 必须注入 REDUCE_PREAMBLE (尾部追加会被 30K 保头截尾吃掉)",
            )

    def test_preamble_prepended_before_truncation(self):
        prepend = 'REDUCE_DATA="${REDUCE_PREAMBLE}${REDUCE_DATA}"'
        self.assertIn(prepend, self.src)
        self.assertLess(
            self.src.index(prepend),
            self.src.index("REDUCE_MULTI_MATERIAL=$(echo \"$REDUCE_DATA\" | utf8_truncate 30000)"),
            "前插必须发生在 30K 截断之前",
        )

    def test_truncation_preserves_radar_behavior(self):
        """行为级对照: 50K 大块 + preamble 前插 → 截断后 radar 仍在; 旧尾部顺序复现血案。"""
        sim = r'''
utf8_truncate() { python3 -c "
import sys
sys.stdout.buffer.write(sys.stdin.buffer.read()[:int(sys.argv[1])])" "$1"; }
BIG="$(head -c 50000 /dev/zero | tr "\0" "x")"
PRE="RADAR_MARKER_XYZ"
NEW="${PRE}${BIG}"; OLD="${BIG}${PRE}"
echo "$NEW" | utf8_truncate 30000 | grep -q RADAR_MARKER_XYZ && echo NEW_OK
echo "$OLD" | utf8_truncate 30000 | grep -q RADAR_MARKER_XYZ || echo OLD_LOST
'''
        r = subprocess.run(["bash", "-c", sim], capture_output=True, text=True, timeout=30)
        self.assertIn("NEW_OK", r.stdout, "前插后 radar 必须在截断素材内")
        self.assertIn("OLD_LOST", r.stdout, "旧尾部顺序 radar 被截掉 (血案对照)")

    def test_dead_code_retired(self):
        """REDUCE_PROMPT / REDUCE_SYSTEM (80K 死路径) 不得回归。"""
        self.assertNotIn('REDUCE_PROMPT="$REDUCE_INTRO', self.src)
        self.assertNotIn('REDUCE_SYSTEM="你是一个在海量数据中寻找蛛丝马迹', self.src)
        self.assertNotRegex(
            self.src, r'REDUCE_MATERIAL=\$\(echo "\$REDUCE_DATA" \| utf8_truncate 80000\)',
            "退役: 80K 截断 (llm_call 无消费)",
        )

    def test_reduce_chars_is_truthful(self):
        """REDUCE_CHARS (dream 产物/last_run 元数据) 必须等于真实发送量。"""
        self.assertIn('REDUCE_CHARS="$REDUCE_MULTI_CHARS"', self.src)

    def test_shared_guards_survive(self):
        """DREAM_HG_GUARD / DREAM_CREDIBILITY 是 DEEP/WIDE 复用的公共注入, 不得被误删。"""
        self.assertIn("DREAM_HG_GUARD=$(python3 -c", self.src)
        self.assertIn("DREAM_CREDIBILITY=$(python3 -c", self.src)
        self.assertEqual(self.src.count("llm_call \"$DEEP_PROMPT\""), 1)
        self.assertEqual(self.src.count("llm_call \"$WIDE_RADAR_PROMPT\""), 1)


# ── (e) top_alignment_picker ─────────────────────────────────────────────


class TestPickerFieldAbsorption(unittest.TestCase):
    def setUp(self):
        import importlib
        import top_alignment_picker
        importlib.reload(top_alignment_picker)
        self.t = top_alignment_picker

    def test_disordered_fields_no_rating_pollution(self):
        """🔴 血案场景: 🎚️ 在 ⭐ 之前时, 评级 5 星不得覆盖对齐 3 星。"""
        content = (
            "📌 标题: 某论文\n"
            "🎚️ 项目对齐度: ⭐⭐⭐ / 与控制平面弱相关\n"
            "⭐ 评级: ⭐⭐⭐⭐⭐\n"
            "🎯 实践启发: xxx"
        )
        r = self.t.parse_alignment_from_content(content)
        self.assertEqual(r["alignment_stars"], 3, "对齐星数被评级污染 (A-F7 血案)")
        self.assertEqual(r["rating_stars"], 5, "评级字段头必须被识别并归 rating")

    def test_normal_order_unchanged(self):
        content = (
            "📌 标题: 某论文\n"
            "⭐ 评级: ⭐⭐⭐⭐\n"
            "🎚️ 项目对齐度: ⭐⭐⭐⭐⭐ / 直接相关\n"
        )
        r = self.t.parse_alignment_from_content(content)
        self.assertEqual(r["alignment_stars"], 5)
        self.assertEqual(r["rating_stars"], 4)

    def test_multiline_alignment_value_still_absorbed(self):
        """原设计场景: alignment 的裸 ⭐ 值行 (无 评级 标签) 仍归 alignment。"""
        r = self.t.parse_alignment_from_content("🎚️ 项目对齐度:\n⭐⭐ / 弱相关\n")
        self.assertEqual(r["alignment_stars"], 2)

    def test_first_star_line_wins_defense_in_depth(self):
        """第二层防御: 即使尾随 ⭐ 行漏进 alignment 文本, 首行星数不被覆盖。"""
        r = self.t.parse_alignment_from_content(
            "🎚️ 项目对齐度: ⭐⭐ / 弱相关\n补充说明含 ⭐⭐⭐⭐⭐ 引用他文评级\n"
        )
        self.assertEqual(r["alignment_stars"], 2, "全文最长段语义是污染放大器, 必须取首个含 ⭐ 行")


class TestMarkers(unittest.TestCase):
    def test_v37_9_314_markers(self):
        for name in ("openclaw_backup.sh", "crontab_safe.sh", "upgrade_openclaw.sh",
                     "kb_dream_helpers.py", "cross_source_signal_aggregator.py",
                     "kb_write.sh", "kb_dream.sh", "top_alignment_picker.py"):
            with self.subTest(file=name):
                self.assertIn("V37.9.314", _read(name), f"{name} 缺 V37.9.314 marker")


if __name__ == "__main__":
    unittest.main(verbosity=2)
