#!/usr/bin/env python3
"""V37.9.356 — 货代 L2「解析率」判据重定义守卫（三层格式而非评级数/新闻数比值）。

血案（2026-09-22 Mac Mini 实录）：
  - 2026-09-21 14:00 货代 run 被 L2 判 parse_low → exit 2 → 整份分析在 Step 4 推送前
    被丢弃，用户当天零货代报告，只收到「解析成功率低」告警；次日 preflight 报
    「货代 deep_dive 合法跳过（LLM 解析率 < 50%）」。
  - 2026-09-22 14:00 一份格式完全正确的输出（三层 header 齐全、零 ASCII 冒号）对
    15 条新闻只评出 8 条「评级：」行；旧阈值 NEW_COUNT/2 = 7 → 仅多 1 条过线。
  - 根因：旧判据是 V25「一条新闻一个评级」时代的解析率代理；V37.9.33 改为三层分类
    （每层 ≤5 条、允许空层）后，分子变成「LLM 认为值得评级的信号数」，分母仍是新闻数
    → 诚实的低信号日被当成解析失败并丢掉有效报告（正确输出被判故障）。
  - 新判据对齐 Step 4 的真实解析契约（按行首 📊/🏢/🚢 切三段）：三段标记齐全 +
    正文非空（有评级行，或三层都显式声明「本期无显著」）。

守卫方法（V37.9.337 惯例）：从 run_freight.sh 真源码抽 L2 块，在 stub 环境跑真 bash，
按输出形态逐一断言 rc / 状态文件 / 告警，防守卫与实现漂移。
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
FREIGHT = os.path.join(ROOT, "jobs", "freight_watcher", "run_freight.sh")
PREFLIGHT = os.path.join(ROOT, "preflight_check.sh")

HEADER_1 = "📊 【第一层：经济晴雨表】运价指数 / 港口吞吐量 / 海关数据"
HEADER_2 = "🏢 【第二层：运营信号】班轮公司 / 港口拥堵 / 路线变化"
HEADER_3 = "🚢 【第三层：商机条目】具体客户/采购信号"
SENTINEL_1 = "📊 本期无显著经济晴雨表信号"
SENTINEL_2 = "🏢 本期无显著运营信号"
SENTINEL_3 = "🚢 本期无显著商机条目"


def _src(path=FREIGHT):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _l2_block(src=None):
    """抽 run_freight.sh 的 L2 块：从 `# L2检查` 行到其后第一个列 0 的 `fi`。"""
    lines = (src or _src()).splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("# L2检查"))
    end = next(i for i in range(start, len(lines)) if lines[i] == "fi")
    block = "\n".join(lines[start:end + 1])
    # 防空转：抽到的必须是真 L2 块
    assert "skipped_parse_low" in block and "exit 2" in block, "L2 block extraction failed"
    return block


def _exec_lines(text):
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


def _layered(ratings=(3, 3, 2), sentinels=(False, False, False), headers=(True, True, True)):
    """按三层格式合成 LLM 输出。ratings[i] 为该层评级条目数；sentinels[i] 为该层空层声明。"""
    out = []
    specs = [
        (HEADER_1, SENTINEL_1, "指数：SCFI 上涨 — 摘要", "解读：需求回暖"),
        (HEADER_2, SENTINEL_2, "动作：MAERSK - 亚欧线 blank sailing", "影响：短期运价支撑"),
        (HEADER_3, SENTINEL_3, "企业信号：某电商 — 旺季备货", "行动：跟进报价"),
    ]
    for i, (hdr, sent, l1, l2) in enumerate(specs):
        if headers[i]:
            out.append(hdr)
        if sentinels[i]:
            out.append(sent)
        for k in range(ratings[i]):
            out.append(f"{k + 1}. {l1}")
            out.append(l2)
            out.append("评级：" + "⭐" * (3 + (k % 3)))
            out.append("")
        out.append("")
    return "\n".join(out)


def _run_l2(llm_out, new_count, env_extra=None):
    """在 stub 环境跑真 L2 块。返回 (rc, stdout, status_dict_or_None, notify_lines)."""
    with tempfile.TemporaryDirectory() as tmp:
        status = os.path.join(tmp, "last_run.json")
        notify_log = os.path.join(tmp, "notify.log")
        raw = os.path.join(tmp, "llm_raw_last.txt")
        out_file = os.path.join(tmp, "llm_out.txt")
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(llm_out)
        script = (
            "set -eo pipefail\n"
            "notify() { printf '%s\\n' \"$1\" >> \"$NOTIFY_LOG\"; }\n"
            "DAY=2026-09-22\n"
            "TS=\"2026-09-22 14:00:00\"\n"
            f"LLM_RAW=\"{raw}\"\n"
            f"STATUS_FILE=\"{status}\"\n"
            f"NEW_COUNT={new_count}\n"
            "LLM_OUT=\"$(cat \"$LLM_OUT_FILE\")\"\n"
            + _l2_block() + "\n"
            "echo PASSED_L2\n"
        )
        sp = os.path.join(tmp, "l2.sh")
        with open(sp, "w", encoding="utf-8") as f:
            f.write(script)
        env = dict(os.environ, LLM_OUT_FILE=out_file, NOTIFY_LOG=notify_log)
        if env_extra:
            env.update(env_extra)
        p = subprocess.run(["bash", sp], capture_output=True, text=True, timeout=60, env=env)
        st = None
        if os.path.isfile(status):
            with open(status, encoding="utf-8") as f:
                st = json.load(f)
        nl = []
        if os.path.isfile(notify_log):
            with open(notify_log, encoding="utf-8") as f:
                nl = [l.rstrip("\n") for l in f if l.strip()]
        return p.returncode, p.stdout + p.stderr, st, nl


class TestL2FormatJudgement(unittest.TestCase):
    """行为级：真 L2 块 × 六种输出形态。"""

    def test_blood_case_honest_low_signal_day_not_discarded(self):
        """血案回归：三层格式正确但只评出 5 条（15 条新闻）→ 不得判 parse_low。"""
        out = _layered(ratings=(2, 2, 1))
        self.assertEqual(out.count("评级："), 5)
        # 反向证据：旧比值判据在同一输入上必定开火（5 < 15//2），证明本测试非空转
        self.assertLess(5, 15 // 2)
        rc, log, st, nl = _run_l2(out, 15)
        self.assertEqual(rc, 0, log)
        self.assertIn("PASSED_L2", log)
        self.assertIsNone(st, "低信号日不得写 parse_low 状态")
        self.assertEqual(nl, [], "低信号日不得发告警")

    def test_real_2026_09_22_shape_passes(self):
        """2026-09-22 实录形态：三层齐全 + 8 条评级 / 15 条新闻 → 通过（旧判据仅多 1 条过线）。"""
        out = _layered(ratings=(3, 3, 2))
        self.assertEqual(out.count("评级："), 8)
        rc, log, st, nl = _run_l2(out, 15)
        self.assertEqual(rc, 0, log)
        self.assertIsNone(st)

    def test_all_empty_layers_honest_day_passes(self):
        """三层都显式「本期无显著」且零评级 = 诚实的无信号日 → 通过，不丢报告。"""
        out = _layered(ratings=(0, 0, 0), sentinels=(True, True, True))
        self.assertEqual(out.count("评级："), 0)
        self.assertEqual(out.count("本期无显著"), 3)
        rc, log, st, nl = _run_l2(out, 15)
        self.assertEqual(rc, 0, log)
        self.assertIsNone(st)
        self.assertEqual(nl, [])

    def test_garbage_output_still_parse_low(self):
        """检出力不减：无三层标记的散文 → parse_low + exit 2 + 状态文件 + 告警。"""
        out = "本期货代市场整体平稳，运价小幅波动。\n建议关注亚欧线舱位变化。\n"
        rc, log, st, nl = _run_l2(out, 15)
        self.assertEqual(rc, 2, log)
        self.assertNotIn("PASSED_L2", log)
        self.assertIsNotNone(st)
        self.assertEqual(st["status"], "parse_low")
        self.assertEqual(st["deep_dive"], "skipped_parse_low")
        self.assertEqual(st["parse_ok"], 0)
        self.assertEqual(st["layers_ok"], 0)
        self.assertEqual(st["new"], 15)
        self.assertEqual(len(nl), 1)
        self.assertIn("格式异常", nl[0])
        self.assertIn("三层段落 0/3", nl[0])
        self.assertIn("请查", nl[0])

    def test_missing_one_section_parse_low(self):
        """只有两段标记（缺 🏢）→ parse_low，layers_ok=2。"""
        out = _layered(ratings=(3, 3, 2), headers=(True, False, True))
        self.assertNotIn("🏢", out)
        rc, log, st, nl = _run_l2(out, 15)
        self.assertEqual(rc, 2, log)
        self.assertEqual(st["layers_ok"], 2)
        self.assertEqual(st["deep_dive"], "skipped_parse_low")

    def test_headers_without_body_parse_low(self):
        """三段标记齐但零评级零空层声明（LLM 只抄了模板头）→ parse_low。"""
        out = _layered(ratings=(0, 0, 0))
        self.assertEqual(out.count("评级："), 0)
        self.assertEqual(out.count("本期无显著"), 0)
        rc, log, st, nl = _run_l2(out, 15)
        self.assertEqual(rc, 2, log)
        self.assertEqual(st["layers_ok"], 3)
        self.assertEqual(st["parse_ok"], 0)
        self.assertIn("空层声明 0/3", nl[0])

    def test_partial_sentinels_with_ratings_passes(self):
        """两层空层声明 + 一层有评级 = 常见诚实日 → 通过。"""
        out = _layered(ratings=(0, 0, 2), sentinels=(True, True, False))
        rc, log, st, nl = _run_l2(out, 15)
        self.assertEqual(rc, 0, log)
        self.assertIsNone(st)

    def test_small_batch_never_parse_low(self):
        """NEW_COUNT ≤ 2 时既有守卫保留：连散文都不判 parse_low。"""
        rc, log, st, nl = _run_l2("随便一句话\n", 2)
        self.assertEqual(rc, 0, log)
        self.assertIsNone(st)

    def test_c_locale_byte_matching_like_cron(self):
        """cron `bash -lc` 常无 UTF-8 locale：LC_ALL=C 下三段标记仍按字节匹配（Mac Mini 部署形态）。"""
        out = _layered(ratings=(2, 2, 1))
        rc, log, st, nl = _run_l2(out, 15, env_extra={"LC_ALL": "C", "LANG": "C"})
        self.assertEqual(rc, 0, log)
        self.assertIsNone(st)
        rc2, _, st2, _ = _run_l2("散文\n", 15, env_extra={"LC_ALL": "C", "LANG": "C"})
        self.assertEqual(rc2, 2)
        self.assertEqual(st2["layers_ok"], 0)

    def test_parse_low_status_keeps_v9_31_consumer_contract(self):
        """状态文件仍写 status=parse_low + deep_dive=skipped_parse_low + 整数 parse_ok（watchdog/observer/evening/preflight 枚举不变）。"""
        rc, log, st, nl = _run_l2("散文\n", 15)
        self.assertEqual(st["status"], "parse_low")
        self.assertEqual(st["deep_dive"], "skipped_parse_low")
        self.assertIsInstance(st["parse_ok"], int)
        self.assertIsInstance(st["layers_ok"], int)
        self.assertIn("time", st)


class TestSourceGuards(unittest.TestCase):
    """源码级：比值判据退役 + 新判据要素在位 + 跨块契约。"""

    @classmethod
    def setUpClass(cls):
        cls.src = _src()
        cls.block = _l2_block(cls.src)
        cls.block_exec = _exec_lines(cls.block)
        cls.preflight = _src(PREFLIGHT)

    def test_ratio_rule_retired(self):
        """L2 可执行行不得再有 NEW_COUNT / 2 比值判据（注释里可留血案叙述）。"""
        self.assertNotIn("NEW_COUNT / 2", self.block_exec)
        self.assertNotIn("解析成功率低", self.block_exec)
        # 防空转：注释确实记录了退役理由
        self.assertIn("NEW_COUNT/2", self.block)

    def test_new_judgement_elements_present(self):
        self.assertIn("LAYERS_OK", self.block_exec)
        self.assertIn("本期无显著", self.block_exec)
        self.assertIn('grep -c "^${_mk}"', self.block_exec)
        self.assertIn("[ \"$NEW_COUNT\" -gt 2 ]", self.block_exec)

    def test_alert_message_carries_diagnostics(self):
        self.assertIn("三层段落 ${LAYERS_OK}/3", self.block_exec)
        self.assertIn("评级 ${PARSE_OK} 行", self.block_exec)
        self.assertIn("空层声明 ${SENTINELS}/3", self.block_exec)

    def test_status_printf_schema(self):
        m = re.search(r"printf '([^']*skipped_parse_low[^']*)'", self.block_exec)
        self.assertIsNotNone(m)
        fmt = m.group(1)
        for key in ('"status":"parse_low"', '"parse_ok":%d', '"layers_ok":%d', '"deep_dive":"skipped_parse_low"'):
            self.assertIn(key, fmt)

    def test_markers_match_step4_split_contract(self):
        """MR-8：L2 检查的三段标记必须与 Step 4 切段正则用的标记一致。"""
        m = re.search(r"re\.split\(r'\(\?=([^']+)'", self.src)
        self.assertIsNotNone(m, "Step 4 section split regex not found")
        split_alts = m.group(1)
        for mk in ("📊", "🏢", "🚢"):
            self.assertIn("\\n" + mk, split_alts)
            self.assertIn(mk, self.block_exec)

    def test_preflight_text_evolved(self):
        line = next(l for l in self.preflight.splitlines() if "skipped_parse_low)" in l)
        idx = self.preflight.splitlines().index(line)
        body = "\n".join(self.preflight.splitlines()[idx:idx + 3])
        self.assertIn("warn", body)
        self.assertIn("格式异常", body)
        self.assertNotIn("解析率 < 50%", body)

    def test_marker_and_syntax(self):
        self.assertIn("V37.9.356", self.src)
        for path in (FREIGHT, PREFLIGHT):
            p = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=1)
