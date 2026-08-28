#!/usr/bin/env python3
"""test_v37_9_331_drill_forensics_honesty.py — V37.9.331 对抗审计守卫

镜头（承接 V37.9.328「声称采集/跑过的东西真的发生了吗」）：三个从未被审的
演练/取证/打标工具，各自在「声称验证/采集/推断」与「实际发生」之间断了账：

  F1  gameday 场景 4 把 slo_checker rc=3（V37.9.322「无数据可判」的诚实结果）
      判成 ❌「SLO 检查异常」— preflight 在 V37.9.322 同步了 exit 3 → warn，
      gameday 这个消费方漏网（原则 #31 跨消费者同步缺口）。
  F2  gameday 场景 3 按「file not found」子串数快照日志 — V37.9.325 把缺失
      文案改成「[no log file found; tried: ...]」后，4/4 全缺失被数成
      「快照包含 4 个日志文件内容」（dev 探针实录）。修为读 logs_meta.resolved
      （生产端自己的账）。
  F3  gameday 场景 2 按 dict 解析 adapter /health 的 circuit_breaker —
      实际是裸字符串（CircuitBreaker.state()）→ AttributeError → 输出恒空 →
      永远 skip「adapter 可能版本较旧」= 断路器验证自 V33 起结构性空转。
  F4  movespeed_incident_capture 把「No such file or directory」（卷未挂载 =
      事故证据本身）标成 [tool_unavailable]（采集器缺工具）→ 最强取证信号被
      埋进错误的桶（V37.9.281 NOT-F2 裸 not-found 分类器同族）。新增
      [target_missing] 标记 + 分析器三个 classify_* 对应桶。
  F5  kb_autotag 裸子串匹配 — "ai" 命中 maintain/raised、"rag" 命中 storage、
      "rest" 命中 interest → 财经笔记两个标签全错（技术/AI+技术/编程），而
      kb_write.sh:16 让它站在每条自动打标 KB note 的生产写入路径上
      （V37.9.316 DC-1 词边界血案同族）。

测试模式：行为级优先 — gameday 的场景块/内嵌 python 从真源码提取后真跑
（防守卫-实现漂移，V37.9.300/328 惯例）；capture.sh 的 python heredoc 提取
后喂真实 macOS 风格 stderr；kb_autotag 直接 import 跑血案探针。
源码断言先剥注释行（V37.9.178 家族：修复注释里描述了被退役的形态）。
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
GAMEDAY = os.path.join(_HERE, "gameday.sh")
CAPTURE = os.path.join(_HERE, "movespeed_incident_capture.sh")
ANALYZER = os.path.join(_HERE, "movespeed_incident_analyzer.py")
ADAPTER = os.path.join(_HERE, "adapter.py")
SNAPSHOT = os.path.join(_HERE, "incident_snapshot.py")
AUTOTAG = os.path.join(_HERE, "kb_autotag.py")

sys.path.insert(0, _HERE)


def _read(path):
    with open(path, encoding="utf-8") as fp:
        return fp.read()


def _strip_comment_lines(text):
    """源码断言前剥 #-注释行（V37.9.178 家族：注释里合法描述退役形态）。"""
    return "\n".join(
        ln for ln in text.splitlines() if not ln.lstrip().startswith("#")
    )


# ───────────────────────────────────────────────────────────────────────
# 提取器（从真源码切块，防守卫与实现漂移）
# ───────────────────────────────────────────────────────────────────────

def _extract_scenario_block(src, title_anchor):
    """提取 run_scenario 里某场景的 case 体（从场景标题注释行到其 ;; 终止符）。"""
    i = src.index(title_anchor)
    start = src.rfind("\n", 0, i) + 1
    end = src.index(";;", start)
    return src[start:end]


def _extract_inline_py(src, start_anchor, end_anchor):
    i = src.index(start_anchor) + len(start_anchor)
    j = src.index(end_anchor, i)
    return src[i:j]


def _run_scenario4(tmp, stub_rc):
    """把 gameday 场景 4 的真实 case 体放进函数壳里跑（stub HOME + stub slo_checker）。"""
    src = _read(GAMEDAY)
    block = _extract_scenario_block(src, "场景 4：SLO 检查器验证")
    # 防空转：切到的必须是真场景体
    assert "slo_checker.py" in block and "SLO_RC" in block, "scenario-4 提取空转"

    home = os.path.join(tmp, f"home_rc{stub_rc}")
    os.makedirs(home, exist_ok=True)
    with open(os.path.join(home, "proxy_stats.json"), "w") as fp:
        json.dump({"slo": {}, "total_requests": 0}, fp)
    with open(os.path.join(home, "slo_checker.py"), "w") as fp:
        fp.write(
            "import sys, os\n"
            "rc = int(os.environ.get('STUB_SLO_RC', '0'))\n"
            "if rc == 0 and '--alert' not in sys.argv:\n"
            "    print('{\"results\": []}')\n"
            "if rc == 2 and '--alert' in sys.argv:\n"
            "    print('stub violation')\n"
            "sys.exit(rc)\n"
        )
    harness = (
        "set -uo pipefail\n"
        "PASS=0; FAIL=0; SKIP=0\n"
        'pass() { echo "PASS: $1"; PASS=$((PASS+1)); }\n'
        'fail() { echo "FAIL: $1"; FAIL=$((FAIL+1)); }\n'
        'skip() { echo "SKIP: $1"; SKIP=$((SKIP+1)); }\n'
        "main() {\n" + block + "\n}\n"
        "main\n"
    )
    script = os.path.join(tmp, f"h_rc{stub_rc}.sh")
    with open(script, "w") as fp:
        fp.write(harness)
    env = dict(os.environ, HOME=home, STUB_SLO_RC=str(stub_rc))
    proc = subprocess.run(
        ["bash", script], capture_output=True, text=True, env=env, timeout=60
    )
    return proc.stdout


class TestGamedaySloRcHandling(unittest.TestCase):
    """F1: rc=3 是诚实结果非 checker 异常；rc=1/2 检出力不减。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="v331_slo_")

    def test_rc3_is_skip_not_fail(self):
        """血案回归：rc=3（无数据可判）不得再报「SLO 检查异常」。"""
        out = _run_scenario4(self.tmp, 3)
        self.assertNotIn("SLO 检查异常", out)
        self.assertIn("SKIP:", out)
        self.assertIn("无数据可判", out)

    def test_rc3_alert_mode_is_pass(self):
        out = _run_scenario4(self.tmp, 3)
        self.assertIn("SLO --alert 模式正常 (rc=3)", out)
        self.assertNotIn("--alert 模式异常", out)

    def test_rc2_violation_still_fails(self):
        """检出力不减：真违规仍 fail。"""
        out = _run_scenario4(self.tmp, 2)
        self.assertIn("FAIL: SLO 有违规项", out)

    def test_rc1_real_breakage_still_fails(self):
        """检出力不减：checker 真异常（rc=1）仍报检查异常。"""
        out = _run_scenario4(self.tmp, 1)
        self.assertIn("SLO 检查异常 (rc=1)", out)
        self.assertIn("FAIL:", out)

    def test_rc0_all_pass(self):
        out = _run_scenario4(self.tmp, 0)
        self.assertIn("PASS: SLO 全部达标", out)
        self.assertNotIn("FAIL:", out)


class TestGamedaySnapshotLogCount(unittest.TestCase):
    """F2: 快照日志计数改读 logs_meta.resolved（生产端自己的账）。"""

    def _count(self, snapshot_dict):
        src = _read(GAMEDAY)
        body = _extract_inline_py(src, 'HAS_LOGS=$(python3 -c "', '" "$FILE"')
        self.assertIn("logs_meta", body, "HAS_LOGS 提取空转")
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False
        ) as fp:
            json.dump(snapshot_dict, fp)
            path = fp.name
        try:
            proc = subprocess.run(
                [sys.executable, "-c", body, path],
                capture_output=True, text=True, timeout=30,
            )
            return proc.stdout.strip()
        finally:
            os.unlink(path)

    def test_all_missing_counts_zero(self):
        """血案回归：4/4 缺失日志（V37.9.325 新文案）必须数成 0，不是 4。"""
        snap = {
            "logs": {
                k: f"[no log file found; tried: /x/{k}.log]"
                for k in ("proxy", "adapter", "gateway", "deploy")
            },
            "logs_meta": {
                k: {"resolved": None, "candidates_tried": [f"/x/{k}.log"]}
                for k in ("proxy", "adapter", "gateway", "deploy")
            },
        }
        self.assertEqual(self._count(snap), "0")

    def test_resolved_logs_counted(self):
        snap = {
            "logs": {"proxy": "tail...", "gateway": "[no log file found; tried: /x]"},
            "logs_meta": {
                "proxy": {"resolved": "/x/tool_proxy.log", "candidates_tried": ["/x/tool_proxy.log"]},
                "adapter": {"resolved": "/x/adapter.log", "candidates_tried": ["/x/adapter.log"]},
                "gateway": {"resolved": None, "candidates_tried": ["/x"]},
            },
        }
        self.assertEqual(self._count(snap), "2")

    def test_old_substring_criterion_would_miscount(self):
        """反向证据（防守卫空转）：证明旧判据在新文案上确实失真 —
        「[no log file found; ...]」不含旧子串，旧法会把缺失数成有内容。"""
        missing_text = "[no log file found; tried: /a, /b]"
        self.assertNotIn("file not found", missing_text)

    def test_producer_text_and_meta_contract(self):
        """MR-8 跨文件契约：incident_snapshot.py 的缺失文案与 logs_meta 键名
        变更时本守卫先红（消费方 gameday 需同步）。"""
        src = _read(SNAPSHOT)
        self.assertIn("[no log file found; tried:", src)
        self.assertIn('"resolved": None', src)
        self.assertIn('"logs_meta"', src)


class TestGamedayBreakerSchema(unittest.TestCase):
    """F3: circuit_breaker 按 adapter 真实 schema（裸字符串）解析。"""

    def _parse(self, health_dict):
        src = _read(GAMEDAY)
        body = _extract_inline_py(
            src,
            'CB_STATE=$(curl -s --max-time 5 "$ADAPTER_URL/health" | python3 -c "',
            '" 2>/dev/null)',
        )
        self.assertIn("circuit_breaker", body, "CB 提取空转")
        proc = subprocess.run(
            [sys.executable, "-c", body],
            input=json.dumps(health_dict),
            capture_output=True, text=True, timeout=30,
        )
        return proc.stdout.strip(), proc.returncode

    def test_string_schema_parses(self):
        """血案回归：现行 adapter 的裸字符串形态必须解析成功（旧 dict 假设
        对字符串 AttributeError → 输出恒空 → 场景永远 skip）。"""
        for state in ("closed", "open", "half-open"):
            out, rc = self._parse({"ok": True, "circuit_breaker": state})
            self.assertEqual(rc, 0)
            self.assertEqual(out, state)

    def test_absent_when_no_fallback_chain(self):
        out, _ = self._parse({"ok": True})
        self.assertEqual(out, "absent")

    def test_dict_schema_forward_compat(self):
        out, _ = self._parse({"circuit_breaker": {"state": "closed"}})
        self.assertEqual(out, "closed")

    def test_open_state_branch_fails_drill(self):
        """检出力：断路器打开必须仍是 fail 分支（源码级：open → fail）。"""
        src = _strip_comment_lines(_read(GAMEDAY))
        self.assertRegex(
            src,
            r'\[ "\$CB_STATE" = "open" \];\s*then\n\s*fail "断路器已打开',
        )

    def test_adapter_contract_state_is_string(self):
        """MR-8 跨文件契约：adapter /health 的 circuit_breaker 由 state() 直出，
        且 state() 只返回三种字符串。adapter 未来改 schema 时本契约先红。"""
        src = _read(ADAPTER)
        self.assertIn('info["circuit_breaker"] = _circuit_breaker.state()', src)
        i = src.index("def state(self):")
        body_lines = []
        for ln in src[i:].splitlines()[1:]:
            if ln.strip() == "" or ln.startswith((" ", "\t")):
                body_lines.append(ln)
            else:
                break  # 首个列-0 行 = 方法体结束（防贪婪越界吞模块级代码）
        body = "\n".join(body_lines)
        self.assertTrue(body.strip(), "CircuitBreaker.state() body extraction empty")
        for lit in ('return "closed"', 'return "half-open"', 'return "open"'):
            self.assertIn(lit, body)
        self.assertNotIn("return {", body)


class TestCaptureTargetMissing(unittest.TestCase):
    """F4: capture.sh 的 stderr 分类 — 卷不存在 ≠ 采集器缺工具。"""

    @classmethod
    def setUpClass(cls):
        src = _read(CAPTURE)
        anchor = "\"$_TMP\" <<'PYEOF'"
        i = src.index(anchor) + len(anchor)
        # 锚点行尾还有 >> "$INCIDENT_FILE" 重定向 — heredoc 体从下一行开始
        i = src.index("\n", i) + 1
        j = src.index("\nPYEOF", i)
        cls.heredoc = src[i:j]
        assert "read_file_with_stderr" in cls.heredoc, "heredoc 提取空转"
        cls.tmp = tempfile.mkdtemp(prefix="v331_cap_")
        cls.script = os.path.join(cls.tmp, "heredoc.py")
        with open(cls.script, "w") as fp:
            fp.write(cls.heredoc)

    def _record(self, files):
        td = tempfile.mkdtemp(dir=self.tmp)
        for name, content in files.items():
            with open(os.path.join(td, name), "w") as fp:
                fp.write(content)
        proc = subprocess.run(
            [sys.executable, self.script, "2026-08-28T00:00:00Z", "t.sh", "23", td],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_volume_missing_is_target_missing_not_tool(self):
        """血案回归：SSD 未挂载时 ls 的 stderr（macOS 原文）必须标
        [target_missing]，不得再标 [tool_unavailable]。"""
        rec = self._record({
            "acl_top": "",
            "acl_top_err": "ls: /Volumes/MOVESPEED/: No such file or directory",
        })
        self.assertTrue(rec["acl_top"].startswith("[target_missing]"), rec["acl_top"])
        self.assertNotIn("[tool_unavailable]", rec["acl_top"])

    def test_lsof_on_missing_volume_is_target_missing(self):
        rec = self._record({
            "lsof": "",
            "lsof_err": "lsof: status error on /Volumes/MOVESPEED: No such file or directory",
        })
        self.assertTrue(rec["lsof"].startswith("[target_missing]"), rec["lsof"])

    def test_missing_binary_still_tool_unavailable(self):
        """检出力不减：bash 的缺工具原文仍归 tool_unavailable。"""
        rec = self._record({
            "lsof": "",
            "lsof_err": "bash: lsof: command not found",
        })
        self.assertTrue(rec["lsof"].startswith("[tool_unavailable]"), rec["lsof"])

    def test_sandbox_denied_precedence_kept(self):
        """V37.9.81 B 语义保留：TCC 拒绝仍最高优先。"""
        rec = self._record({
            "acl_kb": "",
            "acl_kb_err": "ls: .: Operation not permitted",
        })
        self.assertTrue(rec["acl_kb"].startswith("[sandbox_denied]"), rec["acl_kb"])

    def test_clean_stderr_no_marker(self):
        rec = self._record({
            "snapshots": "com.apple.TimeMachine.2026-08-28-000000.local",
            "snapshots_err": "",
        })
        self.assertFalse(rec["snapshots"].startswith("["), rec["snapshots"])

    def test_capture_e2e_never_fails(self):
        """MUST NEVER FAIL 契约：真跑 capture.sh（dev 上多数诊断工具缺席/卷
        不存在）仍 exit 0 且落一行合法 JSON。"""
        incident = os.path.join(self.tmp, "mi.jsonl")
        env = dict(os.environ, MOVESPEED_INCIDENT_FILE=incident)
        proc = subprocess.run(
            ["bash", CAPTURE, "23", "test_caller.sh"],
            capture_output=True, text=True, env=env, timeout=120,
        )
        self.assertEqual(proc.returncode, 0)
        with open(incident) as fp:
            rec = json.loads(fp.read().strip().splitlines()[-1])
        self.assertEqual(rec["caller"], "test_caller.sh")
        self.assertEqual(rec["exit_code"], "23")


class TestAnalyzerTargetMissingBucket(unittest.TestCase):
    """F4 消费端：分析器识别新桶 + priority + render label。"""

    @classmethod
    def setUpClass(cls):
        import movespeed_incident_analyzer as mia
        cls.mia = mia

    def test_classify_functions_recognize_marker(self):
        self.assertEqual(
            self.mia.classify_acl_anomaly("[target_missing] "), "target_missing")
        self.assertEqual(
            self.mia.classify_handle_holders("[target_missing] x"), "target_missing")
        self.assertEqual(
            self.mia.classify_snapshot_count("[target_missing] x"), "target_missing")

    def test_sandbox_marker_still_wins_its_bucket(self):
        """既有语义零破坏。"""
        self.assertEqual(
            self.mia.classify_acl_anomaly("[sandbox_denied] x"), "sandbox_denied")
        self.assertEqual(
            self.mia.classify_acl_anomaly("[tool_unavailable] x"), "tool_unavailable")

    def test_analyze_winner_prefers_target_missing_over_acl(self):
        """行为级：卷不存在的记录在 by_acl_anomaly 里以 target_missing 呈现
        （priority 5 > acl_deny 4 —— 卷不在时 ACL 内容无从谈起）。"""
        records = [{
            "timestamp_iso": "2026-08-28T00:00:00Z",
            "caller": "t.sh", "exit_code": "23",
            "acl_top": "[target_missing] ",
            "acl_kb": " 0: group:everyone deny add_file",
        }]
        out = self.mia.analyze(records)
        self.assertIn("target_missing", out["by_acl_anomaly"])
        self.assertEqual(out["by_acl_anomaly"]["target_missing"], 1)

    def test_render_labels_exist(self):
        """三张 explain map 都有 target_missing 标签（未知桶裸名可读性差）。"""
        src = _read(ANALYZER)
        for anchor in ("acl_explain = {", "handle_explain = {", "snap_explain = {"):
            i = src.index(anchor)
            j = src.index("}", i)
            self.assertIn("target_missing", src[i:j],
                          f"{anchor} 缺 target_missing 标签")

    def test_marker_parity_across_files(self):
        """MR-8：marker 字面量必须同时在生产端与消费端（任一侧单改即红）。"""
        self.assertIn("[target_missing]", _read(CAPTURE))
        analyzer_code = _strip_comment_lines(_read(ANALYZER))
        self.assertEqual(
            analyzer_code.count('startswith("[target_missing]")'), 3,
            "三个 classify_* 必须都识别 target_missing")


class TestAutotagWordBoundary(unittest.TestCase):
    """F5: 词边界匹配 — 血案回归 + 检出力不减。"""

    @classmethod
    def setUpClass(cls):
        import kb_autotag
        cls.k = kb_autotag

    def test_blood_finance_note_no_false_ai(self):
        """血案回归（2026-08-28 探针实录）：财经笔记不得再拿到 技术/AI +
        技术/编程 假标签（raised→ai / interest→rest 子串命中）。"""
        tags = self.k.infer_tags(
            "Central bank raised interest rates; inflation outlook uncertain")
        self.assertNotIn("技术/AI", tags)
        self.assertNotIn("技术/编程", tags)

    def test_blood_cooking_note_no_false_tags(self):
        tags = self.k.infer_tags(
            "Email from John: the storage room needs cleaning, specifically the fridge")
        self.assertNotIn("技术/AI", tags)      # Email→ai
        self.assertNotIn("物流/货代", tags)    # specifically→cif

    def test_blood_health_note_primary_correct(self):
        tags = self.k.infer_tags(
            "Weekly summary: maintain sleep quality and diet, stay active")
        self.assertEqual(tags, ["生活/健康"])  # maintain→ai 不再抢主标签

    def test_detection_power_ai_note(self):
        """检出力不减：真 AI 内容照常命中。"""
        tags = self.k.infer_tags(
            "New transformer LLM fine-tune with RAG pipeline and tokens")
        self.assertIn("技术/AI", tags)

    def test_detection_power_freight_primary(self):
        tags = self.k.infer_tags(
            "Freight rates for container shipping from Shanghai port")
        self.assertEqual(tags[0], "物流/货代")

    def test_cjk_substring_kept(self):
        """CJK 关键词保持子串语义（中文无词边界）。"""
        tags = self.k.infer_tags("大模型的最新进展")
        self.assertIn("技术/AI", tags)

    def test_plural_and_hyphen_adjacency(self):
        tags = self.k.infer_tags("Managing LLMs and tokens across APIs")
        self.assertIn("技术/AI", tags)
        tags2 = self.k.infer_tags("gpt-4 based rag system")
        self.assertIn("技术/AI", tags2)

    def test_kw_hit_unit_semantics(self):
        h = self.k._kw_hit
        self.assertFalse(h("ai", "maintain the garden"))
        self.assertTrue(h("ai", "the ai model"))
        self.assertFalse(h("rag", "storage average leverage"))
        self.assertTrue(h("rag", "rag pipeline"))
        self.assertFalse(h("api", "rapid growth"))
        self.assertFalse(h("git", "digit legitimate"))
        self.assertTrue(h("git", "git push origin"))
        self.assertTrue(h("大模型", "关于大模型的讨论"))

    def test_retag_apply_accounting(self):
        """原则 #36-4：--apply 的「已更新 N」按实际写入成败对账 — frontmatter
        写失败的条目 applied=False 且 index 不动（一物一形）。"""
        k = self.k
        tmp = tempfile.mkdtemp(prefix="v331_tag_")
        notes = os.path.join(tmp, "notes")
        os.makedirs(notes)
        good = os.path.join("notes", "good.md")
        bad = os.path.join("notes", "bad.md")
        with open(os.path.join(tmp, good), "w") as fp:
            fp.write("---\ntags: [其他/未分类]\n---\ntransformer llm paper\n")
        with open(os.path.join(tmp, bad), "w") as fp:
            fp.write("no frontmatter here — transformer llm paper\n")
        index = {"entries": [
            {"file": good, "tags": ["其他/未分类"], "summary": "g"},
            {"file": bad, "tags": ["其他/未分类"], "summary": "b"},
        ]}
        with open(os.path.join(tmp, "index.json"), "w") as fp:
            json.dump(index, fp)

        old = (k.KB_BASE, k.INDEX_FILE, k.NOTES_DIR)
        try:
            k.KB_BASE = tmp
            k.INDEX_FILE = os.path.join(tmp, "index.json")
            k.NOTES_DIR = notes
            changes = k.retag_all(apply=True)
        finally:
            k.KB_BASE, k.INDEX_FILE, k.NOTES_DIR = old

        by_file = {c["file"]: c for c in changes}
        self.assertTrue(by_file[good]["applied"])
        self.assertFalse(by_file[bad]["applied"])
        with open(os.path.join(tmp, "index.json")) as fp:
            saved = json.load(fp)
        saved_by_file = {e["file"]: e for e in saved["entries"]}
        self.assertIn("技术/AI", saved_by_file[good]["tags"])
        self.assertEqual(saved_by_file[bad]["tags"], ["其他/未分类"])


class TestSourceGuards(unittest.TestCase):
    """marker + 退役反模式 + 注册守卫。"""

    def test_gameday_markers_and_retired_patterns(self):
        src = _read(GAMEDAY)
        self.assertIn("V37.9.331", src)
        code = _strip_comment_lines(src)
        # rc=3 分档在位
        self.assertRegex(code, r"\[ \$SLO_RC -eq 3 \]")
        self.assertRegex(code, r"\[ \$ALERT_RC -eq 3 \]")
        # 退役：dict 假设的 CB 解析 与 子串日志计数
        self.assertNotIn("cb.get(\\\"state\\\"", code)
        self.assertNotIn("'file not found' not in v", code)
        # 退役：场景 1 无对账的空 TOTAL pass
        self.assertRegex(code, r'if \[ -n "\$TOTAL" \]')

    def test_capture_classification_shape(self):
        code = _strip_comment_lines(_read(CAPTURE))
        # target_missing 分支紧跟 no-such-file 判据（防占位漂移）
        self.assertRegex(
            code,
            r'if "no such file or directory" in err_lower:\n'
            r'\s*return "\[target_missing\] "',
        )
        # tool_unavailable 收窄后仍有 command-not-found 判据（检出力）
        self.assertRegex(
            code,
            r'if "command not found" in err_lower:\n'
            r'\s*return "\[tool_unavailable\] "',
        )
        # 退役：旧的组合式判据（no-such-file 归 tool_unavailable）
        self.assertNotRegex(
            code,
            r'"no such file or directory" in err_lower\n'
            r'\s*or "not found"',
        )

    def test_autotag_shape(self):
        src = _read(AUTOTAG)
        self.assertIn("V37.9.331", src)
        code = _strip_comment_lines(src)
        # 打分必须走 _kw_hit
        self.assertIn("_kw_hit(kw, text_lower)", code)
        # 退役：裸子串打分形态
        self.assertNotRegex(code, r"if kw\.lower\(\) in text_lower")
        # 词边界必须用显式字符类 lookaround（V37.9.316 \w-含-CJK 理由）
        self.assertIn("(?<![a-z0-9])", code)
        self.assertIn("s?(?![a-z0-9])", code)

    def test_registered_in_full_regression(self):
        src = _read(os.path.join(_HERE, "full_regression.sh"))
        self.assertIn("test_v37_9_331_drill_forensics_honesty.py", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
