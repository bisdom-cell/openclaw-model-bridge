#!/usr/bin/env python3
"""V37.9.78 health_check.sh 系统证据周报 — source-level + runtime 守卫.

V37.9.78 把 health_check.sh 从 v1.1 单薄状态汇报升级为"系统证据周报":
  - 移除冗余段: 任务统计 (与 daily_ops_report 重叠) / Session 历史 (低价值)
  - 新增 5 段证据: SLO 趋势 / 安全评分 / 治理审计 / MOVESPEED 24h incidents / X 监控质量
  - MR-8 single-source-of-truth: 全部走外部工具调用不内嵌采集逻辑
  - 三层 FAIL-OPEN: 工具缺失 / timeout / parse 失败 → 降级显示不阻塞推送
  - safe_call helper 函数统一封装外部工具调用

测试设计:
  - TestSourceGuards: declaration 层字面量守卫 (V37.9.78 marker / 9 段 emoji / 关键字段名)
  - TestSafeCallHelper: helper 函数行为契约 (timeout / 缺失 / fallback)
  - TestRuntimeBehavior: subprocess 真跑 v2.0 验证 8 段 + FAIL-OPEN 降级
  - TestReverseValidation: 反向 sabotage 验证守卫真有效
  - TestGovernanceLineage: 引用关键工具的版本血脉
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPT_PATH = os.path.join(REPO_ROOT, "health_check.sh")


def _read_script():
    with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
        return f.read()


class TestSourceGuards(unittest.TestCase):
    """声明层: V37.9.78 标记 + 9 段 emoji marker + 关键字段名."""

    def setUp(self):
        self.src = _read_script()

    def test_v37_9_78_marker_in_header(self):
        """V37.9.78 标记必须在文件头 comment 段."""
        head = self.src[:1500]
        self.assertIn("V37.9.78", head, "V37.9.78 marker 必须出现在 health_check.sh 头部")
        self.assertIn("系统证据周报", head, "v2.0 重定位文案必须出现")

    def test_safe_call_helper_defined(self):
        """safe_call() 函数必须定义 (MR-8 single-source-of-truth)."""
        self.assertIn("safe_call() {", self.src,
                      "safe_call helper 函数必须定义")
        self.assertIn("timeout 30", self.src,
                      "safe_call 必须含 timeout 30 防挂")

    def test_nine_section_emoji_markers(self):
        """9 段证据 emoji marker 必须都在 REPORT 拼装中出现."""
        required_markers = [
            ("🖥 服务",      "服务健康"),
            ("🤖 模型",      "模型 ID"),
            ("📊 SLO",       "SLO 趋势"),
            ("🛡 安全评分",   "安全评分"),
            ("🏛 治理审计",   "治理审计"),
            ("🛟 MOVESPEED", "MOVESPEED 24h incidents"),
            ("🐦 X 监控",    "X 监控质量"),
            ("📚 知识库",    "知识库"),
            ("💾 外挂 SSD",  "外挂 SSD"),
        ]
        for marker, label in required_markers:
            self.assertIn(marker, self.src,
                          f"{label}段 emoji marker '{marker}' 必须出现")

    def test_slo_uses_dashboard_json_correct_cli(self):
        """SLO 段必须用 `--dashboard --json`, 不能只 `--json` (slo_dashboard CLI 设计要求)."""
        self.assertIn("slo_dashboard.py' --dashboard --json", self.src,
                      "SLO 段必须用 --dashboard --json 组合, 单 --json 不输出")
        # 反模式守卫: 不能存在裸 `slo_dashboard.py' --json` (无 --dashboard 前缀)
        bare_json_pattern = re.compile(r"slo_dashboard\.py' --json")
        # 注意: 允许 `--dashboard --json` 出现, 禁止单独的 `--json`
        for m in bare_json_pattern.finditer(self.src):
            preceding = self.src[max(0, m.start() - 30):m.start()]
            self.assertIn("--dashboard ", preceding,
                          "slo_dashboard.py 必须在 --json 前有 --dashboard")

    def test_slo_field_names_correct(self):
        """SLO 解析字段名必须匹配 slo_dashboard.py 实际输出 schema."""
        # current 路径必须用 d.get("current") 不能用 "current_metrics"
        self.assertNotIn('d.get(\\"current_metrics\\"', self.src,
                         "禁用 current_metrics (v1.1 错配字段), 应用 current")
        self.assertIn('d.get(\\"current\\")', self.src,
                      "必须用 current 而非 current_metrics")
        # 字段名校对
        self.assertIn('cur.get(\\"p95_ms\\"', self.src,
                      "必须用 p95_ms 而非 p95_latency_ms")
        self.assertIn('cur.get(\\"success_pct\\"', self.src,
                      "必须用 success_pct 而非 success_rate_pct")
        self.assertIn('d.get(\\"trend_24h\\"', self.src,
                      "必须用 trend_24h 而非 trend")
        self.assertIn('d.get(\\"overall\\"', self.src,
                      "必须读 overall 字段判定 NO DATA")

    def test_security_score_json_parsing(self):
        """安全评分段必须调 security_score.py --json + 解析 total/max/dimensions."""
        self.assertIn("security_score.py' --json", self.src,
                      "必须调 security_score.py --json")
        self.assertIn('d.get(\\"total\\"', self.src, "必须读 total 字段")
        self.assertIn('d.get(\\"dimensions\\"', self.src, "必须读 dimensions 字段")
        self.assertIn("弱项:", self.src, "弱项列表必须展示")

    def test_governance_audit_reads_metrics_jsonl(self):
        """治理审计段必须读 ontology/.audit_metrics.jsonl."""
        self.assertIn(".audit_metrics.jsonl", self.src,
                      "必须读 audit_metrics.jsonl 历史文件")
        self.assertIn("total_invariants", self.src, "必须读 total_invariants 字段")
        self.assertIn("fail_count", self.src, "必须读 fail_count 字段")
        self.assertIn("error_count", self.src,
                      "必须读 error_count (V37.3 INV-GOV-001 教训 — error 不能吞)")

    def test_movespeed_incidents_uses_monitor_helper(self):
        """MOVESPEED 段必须调 movespeed_incident_monitor.py."""
        self.assertIn("movespeed_incident_monitor.py", self.src,
                      "必须调 movespeed_incident_monitor.py 24h 监控")
        self.assertIn("movespeed_incidents.jsonl", self.src,
                      "必须读 incidents.jsonl 取证累积")

    def test_zombie_dir_uses_correct_path(self):
        """X 监控段必须扫 V37.8.4 INV-X-001 的 zombies_*.txt 文件."""
        self.assertIn("zombies_*.txt", self.src,
                      "必须 find zombies_*.txt (V37.8.4 INV-X-001)")
        self.assertIn(".openclaw/jobs/finance_news/cache", self.src,
                      "必须扫 finance_news cache 目录")
        # 必须是近 7 天 (-mtime -7)
        self.assertIn("-mtime -7", self.src, "必须限定近 7 天累积")

    def test_push_uses_notify_sh_first_fallback_openclaw(self):
        """V37.9.78 改造: 优先 notify.sh (重试+队列), fallback openclaw 直推."""
        self.assertIn('source "$HOME/notify.sh"', self.src,
                      "必须先尝试 source notify.sh")
        self.assertIn('notify "$REPORT" --topic daily', self.src,
                      "必须用 notify --topic daily 路由到 #日报")
        # fallback 路径保留 ($OPENCLAW 变量替代 'openclaw' 字面量)
        self.assertIn("message send --channel discord", self.src,
                      "fallback 必须保留 Discord 通道")
        self.assertIn("message send --channel whatsapp", self.src,
                      "fallback 必须保留 WhatsApp 通道")
        self.assertIn("$OPENCLAW", self.src,
                      "必须用 $OPENCLAW 变量 (v1.1 契约保留)")

    def test_push_fallback_dual_channel_independent(self):
        """V37.8.13 教训: WhatsApp 失败不阻塞 Discord (告警链不依赖失效主体)."""
        # 找 fallback 段
        fb_idx = self.src.find('fallback openclaw')
        self.assertGreater(fb_idx, 0, "fallback 段必须存在")
        # 在 fallback 段, Discord 调用应该独立于 WhatsApp (不在 if 嵌套内)
        fb_block = self.src[fb_idx:fb_idx + 1500]
        # V37.9.78 fallback 用 || true 让两个 send 都独立 exit 0
        # 旧 v1.1 反模式: if openclaw whatsapp; then openclaw discord; fi
        # 应该已经移除
        old_antipattern = re.compile(
            r'if\s+\$OPENCLAW\s+message\s+send\s+--channel\s+whatsapp[^\n]*\n\s*echo',
            re.MULTILINE)
        self.assertIsNone(old_antipattern.search(self.src),
                          "V37.9.78 不应保留 v1.1 反模式 'if WhatsApp 成功 then Discord' (V37.8.13 教训)")

    def test_health_status_json_contract_preserved(self):
        """v1.1 health_status.json 机器可读契约必须保留."""
        self.assertIn("HEALTH_JSON_PATH", self.src,
                      "HEALTH_JSON_PATH env 必须保留 (v1.1 兼容)")
        self.assertIn("health_status.json", self.src,
                      "默认 JSON 路径必须保留")
        self.assertIn('"version": "v37.9.78"', self.src,
                      "JSON version 字段必须 bump 到 v37.9.78")

    def test_removed_v1_1_low_value_sections(self):
        """v1.1 低价值段必须移除: 任务统计 / Session 历史."""
        # 旧的 "任务统计（近7天）" 段移除
        self.assertNotIn("任务统计（近7天）", self.src,
                         "v1.1 任务统计段必须移除 (与 daily_ops_report 重叠)")
        # 旧的 "Session历史" 段移除
        self.assertNotIn("Session历史", self.src,
                         "v1.1 Session 历史段必须移除 (低价值)")
        # 旧的 cron runs subprocess 调用移除
        self.assertNotIn("'cron', 'runs', '--id'", self.src,
                         "v1.1 cron runs 调用必须移除 (与 daily_ops_report 功能重叠)")

    def test_fail_open_descent_text_present(self):
        """三层 FAIL-OPEN 降级文案必须出现 (工具缺失 / 解析失败 / 暂无数据)."""
        self.assertIn("工具不可用", self.src, "必须有'工具不可用'降级文案")
        self.assertIn("暂无", self.src, "必须有'暂无...'降级文案 (历史快照)")
        self.assertIn("解析失败", self.src, "必须有'解析失败'降级文案")

    def test_v37_9_78_hotfix_timeout_fallback_chain(self):
        """V37.9.78-hotfix: safe_call 必须三档 fallback (timeout / gtimeout / 直接跑)
        防 macOS BSD 无 timeout 命令时 SLO/安全/治理全 fallback 到"工具不可用".

        Mac Mini 实测发现 (2026-05-18): macOS 默认无 timeout 命令, 导致 safe_call
        所有调用走 fallback 路径. 修复: command -v 检测三档.
        """
        # 必须含 V37.9.78-hotfix marker
        self.assertIn("V37.9.78-hotfix", self.src,
                      "V37.9.78-hotfix marker 必须在 safe_call 注释中")
        # 必须检测 timeout 命令存在性 (不是无条件用)
        self.assertIn("command -v timeout", self.src,
                      "必须用 command -v 检测 timeout 命令是否存在")
        # 必须含 gtimeout fallback (Homebrew coreutils)
        self.assertIn("command -v gtimeout", self.src,
                      "第二档 fallback 必须用 gtimeout (Homebrew coreutils 装的版本)")
        # 必须含 macOS BSD 注释说明
        self.assertIn("macOS BSD", self.src,
                      "必须显式注释 macOS BSD 默认无 timeout 的根因")
        # 反模式守卫: safe_call 函数体内禁止仅有"无条件 timeout 30"行
        # (不应回退到 V37.9.78 原始无 fallback 版本)
        sc_start = self.src.find("safe_call() {")
        sc_end = self.src.find("}\n", sc_start)
        sc_body = self.src[sc_start:sc_end]
        # 函数体必须含至少 2 个 timeout 引用 (timeout 自身 + command -v timeout 检测)
        timeout_refs = sc_body.count("timeout")
        self.assertGreaterEqual(timeout_refs, 3,
                                f"safe_call 函数体应含 timeout/gtimeout/检测多处引用, 实际={timeout_refs}")


class TestSafeCallHelper(unittest.TestCase):
    """safe_call helper 函数运行时契约: timeout / fallback / 异常吞掉."""

    def _run_safe_call(self, cmd, fallback="FALLBACK"):
        """提取 safe_call() 函数体并 subprocess 真跑."""
        script = f"""
{_read_script_safe_call_only()}
result=$(safe_call '{cmd}' '{fallback}')
echo "$result"
"""
        proc = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, timeout=60
        )
        return proc.stdout.strip(), proc.returncode

    def test_safe_call_success_returns_cmd_output(self):
        """正常命令: 返回 stdout."""
        out, rc = self._run_safe_call("echo HELLO_OK", "FALLBACK_HIT")
        self.assertEqual(out, "HELLO_OK")
        self.assertEqual(rc, 0)

    def test_safe_call_command_fails_returns_fallback(self):
        """命令 exit 非 0: 返回 fallback."""
        out, _ = self._run_safe_call("false", "FALLBACK_HIT")
        self.assertEqual(out, "FALLBACK_HIT")

    def test_safe_call_empty_output_returns_fallback(self):
        """命令 exit 0 但 stdout 为空: 返回 fallback."""
        out, _ = self._run_safe_call("true", "EMPTY_FALLBACK")
        self.assertEqual(out, "EMPTY_FALLBACK")

    def test_safe_call_command_not_found_returns_fallback(self):
        """命令不存在: 返回 fallback (不抛异常)."""
        out, _ = self._run_safe_call(
            "nonexistent_cmd_xyz_v9_78", "NOT_FOUND_FALLBACK")
        self.assertEqual(out, "NOT_FOUND_FALLBACK")

    def test_safe_call_never_propagates_exit_code(self):
        """safe_call 永不抛非 0 exit, caller 用 set -e 也安全."""
        script = f"""
set -e
{_read_script_safe_call_only()}
result=$(safe_call 'false' 'OK_FALLBACK')
echo "caller_survived: $result"
"""
        proc = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(proc.returncode, 0,
                         "set -e caller 不应被 safe_call 杀掉")
        self.assertIn("caller_survived: OK_FALLBACK", proc.stdout)

    def test_v37_9_78_hotfix_command_v_timeout_detection_runtime(self):
        """V37.9.78-hotfix runtime 守卫: 函数体执行 `command -v timeout` 真返回 OK
        (dev linux 有 timeout), 走第一档. 防 helper 内部逻辑被偶发回退.

        注: dev linux 无法真实模拟 macOS 无 timeout 场景 (dev /bin/timeout 存在).
        Mac Mini 真实激活验证由 source-level 守卫 + 部署后 9 段 SLO/安全/治理段不再
        显示"工具不可用"间接证明. 此处只做 runtime 冒烟 — 让 safe_call 真跑一次确保
        没有 syntax error / 函数体能正常执行.
        """
        script = f"""
{_read_script_safe_call_only()}
# dev linux 有 timeout, 应走第一档
result=$(safe_call 'echo SAFE_CALL_RUNTIME_OK' 'FALLBACK_BAD')
echo "$result"
"""
        proc = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(proc.returncode, 0,
                         f"safe_call 不应崩, stderr={proc.stderr}")
        self.assertIn("SAFE_CALL_RUNTIME_OK", proc.stdout,
                      f"safe_call dev 跑应回 cmd 输出, 实际={proc.stdout}")


def _read_script_safe_call_only():
    """提取 health_check.sh 顶部 + safe_call() 函数定义 (用于隔离测试)."""
    src = _read_script()
    # safe_call() 定义到第一个 # === 之前
    sc_start = src.find("safe_call() {")
    sc_end = src.find("# === 1.", sc_start)
    if sc_start < 0 or sc_end < 0:
        return ""
    # 头部 PATH 声明
    return 'export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"\n' + \
           src[sc_start:sc_end]


class TestRuntimeBehavior(unittest.TestCase):
    """subprocess 真跑 v2.0 验证 8 段都出现 + FAIL-OPEN 降级."""

    def test_dev_env_emits_all_9_sections(self):
        """dev 环境跑 health_check.sh 输出必须含 9 段 emoji marker."""
        env = os.environ.copy()
        env["OPENCLAW_REPO_DIR"] = REPO_ROOT
        # 用临时 HEALTH_JSON_PATH 防污染 home
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            env["HEALTH_JSON_PATH"] = tf.name
            json_path = tf.name
        try:
            proc = subprocess.run(
                ["bash", SCRIPT_PATH],
                capture_output=True, text=True, timeout=120, env=env
            )
            # FAIL-OPEN 契约: dev 缺工具不应阻塞 (脚本不一定 exit 0 — fallback openclaw 缺失也可能非 0,
            # 但 stdout 应该至少含 REPORT)
            stdout = proc.stdout
            for marker in ["🖥 服务", "🤖 模型", "📊 SLO", "🛡 安全评分",
                           "🏛 治理审计", "🛟 MOVESPEED", "🐦 X 监控",
                           "📚 知识库", "💾 外挂 SSD"]:
                self.assertIn(marker, stdout,
                              f"dev 环境 stdout 必须含 {marker} 段 (FAIL-OPEN 降级显示)")
            # ✅ 周报完毕 标记必须出现
            self.assertIn("周报完毕", stdout, "周报结尾标记必须出现")
            # health_status.json 应该被写入 (即使工具缺失)
            self.assertTrue(os.path.exists(json_path), "health_status.json 必须落盘")
        finally:
            if os.path.exists(json_path):
                os.unlink(json_path)

    def test_fail_open_graceful_degradation(self):
        """工具缺失场景: 必须显示降级文案 (不抛 Traceback)."""
        env = os.environ.copy()
        # 故意指向不存在的 REPO_DIR
        env["OPENCLAW_REPO_DIR"] = "/tmp/nonexistent_repo_v9_78"
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            env["HEALTH_JSON_PATH"] = tf.name
            json_path = tf.name
        try:
            proc = subprocess.run(
                ["bash", SCRIPT_PATH],
                capture_output=True, text=True, timeout=60, env=env
            )
            stdout = proc.stdout
            # 必须含降级文案,不能含 Traceback
            self.assertNotIn("Traceback", stdout,
                             "FAIL-OPEN 不应让 Python Traceback 进入 REPORT")
            self.assertTrue(
                "工具不可用" in stdout or "暂无" in stdout
                or "解析失败" in stdout or "缺失" in stdout,
                f"必须含降级文案, stdout={stdout[:500]}")
        finally:
            if os.path.exists(json_path):
                os.unlink(json_path)

    def test_health_status_json_schema_v37_9_78(self):
        """health_status.json 必须有 V37.9.78 schema (version/services/model/kb/ssd)."""
        env = os.environ.copy()
        env["OPENCLAW_REPO_DIR"] = REPO_ROOT
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            env["HEALTH_JSON_PATH"] = tf.name
            json_path = tf.name
        try:
            subprocess.run(
                ["bash", SCRIPT_PATH],
                capture_output=True, text=True, timeout=120, env=env
            )
            self.assertTrue(os.path.exists(json_path))
            import json
            with open(json_path) as f:
                data = json.load(f)
            self.assertEqual(data["version"], "v37.9.78",
                             "JSON version 字段必须为 v37.9.78")
            for key in ("timestamp", "services", "model", "kb", "ssd"):
                self.assertIn(key, data, f"必须含 {key} 字段")
            for svc in ("gateway", "adapter", "proxy"):
                self.assertIn(svc, data["services"], f"services 必须含 {svc}")
        finally:
            if os.path.exists(json_path):
                os.unlink(json_path)


class TestReverseValidation(unittest.TestCase):
    """反向 sabotage 验证守卫真有效 (V37.8.x 教训方法论)."""

    def setUp(self):
        # 备份原文件
        self.backup = _read_script()

    def tearDown(self):
        # 恢复原文件
        with open(SCRIPT_PATH, "w", encoding="utf-8") as f:
            f.write(self.backup)

    def _write_sabotaged(self, content):
        with open(SCRIPT_PATH, "w", encoding="utf-8") as f:
            f.write(content)

    def test_sabotage_remove_v37_9_78_marker_caught(self):
        """sabotage 移除全部 V37.9.78 marker → test_v37_9_78_marker_in_header 立即失败.

        注: V37.9.78 marker 在 header 1500 字符内出现多次 (注释 + safe_call docstring),
        sabotage 必须 replace_all 才能让守卫触发. 单次 replace 仍有其他 marker 兜底守卫.
        """
        sabotaged = self.backup.replace("V37.9.78", "V_DISABLED_X")  # replace ALL
        self._write_sabotaged(sabotaged)
        # 运行守卫
        tc = TestSourceGuards()
        tc.setUp()
        with self.assertRaises(AssertionError):
            tc.test_v37_9_78_marker_in_header()

    def test_sabotage_remove_slo_section_caught(self):
        """sabotage 移除 SLO 段 emoji → 9 段守卫立即失败."""
        sabotaged = self.backup.replace("📊 SLO", "DISABLED_SLO")
        self._write_sabotaged(sabotaged)
        tc = TestSourceGuards()
        tc.setUp()
        with self.assertRaises(AssertionError):
            tc.test_nine_section_emoji_markers()

    def test_sabotage_revert_to_v1_1_field_names_caught(self):
        """sabotage 退回 v1.1 错配字段名 (current → current_metrics) → 字段守卫立即失败."""
        sabotaged = self.backup.replace(
            'd.get(\\"current\\")', 'd.get(\\"current_metrics\\")'
        )
        self._write_sabotaged(sabotaged)
        tc = TestSourceGuards()
        tc.setUp()
        with self.assertRaises(AssertionError):
            tc.test_slo_field_names_correct()

    def test_sabotage_remove_safe_call_helper_caught(self):
        """sabotage 删 safe_call helper → safe_call 守卫立即失败."""
        sabotaged = re.sub(
            r"safe_call\(\) \{[^}]+\}",
            "# safe_call removed by sabotage",
            self.backup, count=1, flags=re.DOTALL
        )
        self._write_sabotaged(sabotaged)
        tc = TestSourceGuards()
        tc.setUp()
        with self.assertRaises(AssertionError):
            tc.test_safe_call_helper_defined()


class TestGovernanceLineage(unittest.TestCase):
    """V37.9.78 复用工具的版本血脉引用必须保留 (运维可追溯)."""

    def setUp(self):
        self.src = _read_script()

    def test_references_v36_slo_dashboard(self):
        """V36 slo_dashboard.py 引用必须在注释中标注."""
        self.assertIn("V36 slo_dashboard", self.src,
                      "必须引用 V36 slo_dashboard.py 血脉")

    def test_references_v30_2_security_score(self):
        """V30.2 security_score.py 引用必须在注释中标注."""
        self.assertIn("V30.2 security_score", self.src,
                      "必须引用 V30.2 security_score.py 血脉")

    def test_references_v37_1_audit_metrics(self):
        """V37.1 .audit_metrics.jsonl 引用必须在注释中标注."""
        self.assertIn("V37.1", self.src,
                      "必须引用 V37.1 audit_metrics.jsonl 血脉")

    def test_references_v37_9_27_incident_capture(self):
        """V37.9.27 movespeed_incident_capture 引用必须在注释中标注."""
        self.assertIn("V37.9.27", self.src,
                      "必须引用 V37.9.27 取证机制血脉")

    def test_references_v37_8_4_zombie_inv(self):
        """V37.8.4 INV-X-001 引用必须在注释中标注."""
        self.assertIn("V37.8.4", self.src,
                      "必须引用 V37.8.4 INV-X-001 X 监控质量血脉")
        self.assertIn("INV-X-001", self.src,
                      "必须显式引用 INV-X-001 不变式")

    def test_references_v37_8_13_alert_independence(self):
        """V37.8.13 教训 (告警链不依赖失效主体) 引用必须在 fallback 段注释中."""
        self.assertIn("V37.8.13", self.src,
                      "必须引用 V37.8.13 教训")


if __name__ == "__main__":
    unittest.main()
