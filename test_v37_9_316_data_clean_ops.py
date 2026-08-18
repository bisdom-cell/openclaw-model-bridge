#!/usr/bin/env python3
"""V37.9.316（对抗审计 · data_clean 清洗操作诚实性）守卫.

审计镜头 = 「系统唯一写用户业务数据的工具坏了, 我们发现得了吗」。
四个 grounded 复现的真 bug（探针实录见 changelog V37.9.316）:

  DC-1 (HIGH) op_remove_test 子串匹配 → 真实业务行被当测试数据删除
       实录: 7 行样本删掉 6 行 —— BARCELONA(bar) / Latest(test) /
       Protest(test) / Bar Harbor(bar) / TEMPE(temp) 全灭, 只留 1 行;
       渲染层报「✓ remove_test（删除 6 行）」= fail-plausible 静默数据丢失。
       用户主业是货代, BARCELONA 是真实港口名。
  DC-2 (MED) op_fix_dates 用 rows.index(r) 定位坏行 → 内容相同的重复行
       返回首个下标: 同一行号报两次、真正的坏行永不出现（且 O(n²)）。
  DC-3 (MED) 请求的列不存在时 fix_case/fix_dates 静默跳过 → 渲染成
       「✓ fix_case（修改 0 个单元格）」。V37.9.288 修好了消费端
       (error/skipped 渲染), 但生产端从不设这两个键 = 断链。
  DC-4 (MED) op_dedup_near 假定首列是 ID 却从不披露 —— 首列是日期的表
       会把「同客户同金额不同日期」的第二笔真实订单删掉。
  DC-5 (MED) 前序操作删行后, 后续步骤报的行号是「本步输入」位置而非原文件
       位置。默认链 trim,dedup,fix_dates 就会发生: 坏日期在原文件第 4 行,
       报告写第 3 行（那是一条被删掉的干净重复行）。精确回映射需逐行身份
       追踪 = 新机制, 按日落法只做诚实标注。

守卫 = 行为级（真调 data_clean 函数 + exec-with-mocks 跑 proxy 渲染器）
+ 源码级反模式退役断言。
"""
import ast
import csv
import io
import json
import os
import shutil
import sys
import tempfile
import textwrap
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import data_clean as dc  # noqa: E402

_REPO = os.path.dirname(os.path.abspath(__file__))
_DATA_CLEAN = os.path.join(_REPO, "data_clean.py")
_TOOL_PROXY = os.path.join(_REPO, "tool_proxy.py")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _make_format_tool_result():
    """抽 ProxyHandler._format_tool_result 源码 exec 进隔离 ns（镜像 test_v37_9_288）."""
    src = _read(_TOOL_PROXY)
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    fn_src = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "ProxyHandler":
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == "_format_tool_result":
                    fn_src = textwrap.dedent(
                        "".join(lines[sub.lineno - 1:sub.end_lineno]))
    assert fn_src, "ProxyHandler._format_tool_result 未找到"
    ns = {"json": json, "sys": sys, "os": types.SimpleNamespace(
        path=types.SimpleNamespace(exists=lambda p: True, basename=os.path.basename),
        sep=os.sep, environ=os.environ),
        "log": lambda *a, **k: None}
    exec(compile(fn_src, "<_format_tool_result>", "exec"), ns)
    return ns["_format_tool_result"]


def _fmt_execute(report):
    fn = _make_format_tool_result()
    return fn("data_clean", json.dumps({"action": "execute"}),
              json.dumps(report, ensure_ascii=False))


# ── DC-1: remove_test 词边界 ──────────────────────────────────────────

class TestRemoveTestWordBoundary(unittest.TestCase):
    """DC-1 血案回归: 子串匹配曾删掉真实业务行."""

    HEADERS = ["order_id", "customer", "port", "amount"]
    BLOOD_ROWS = [
        {"order_id": "1", "customer": "ACME Ltd", "port": "BARCELONA", "amount": "100"},
        {"order_id": "2", "customer": "Latest Logistics", "port": "HAMBURG", "amount": "200"},
        {"order_id": "3", "customer": "Protest Shipping", "port": "ROTTERDAM", "amount": "300"},
        {"order_id": "4", "customer": "Real Client", "port": "SINGAPORE", "amount": "400"},
        {"order_id": "5", "customer": "test row please ignore", "port": "X", "amount": "0"},
        {"order_id": "6", "customer": "Contemporary Co", "port": "TEMPE", "amount": "600"},
    ]

    def _run(self, headers, rows):
        return dc.op_remove_test(headers, [dict(r) for r in rows], [])

    def test_blood_case_business_rows_survive(self):
        """2026-08-18 探针实录: 6 行只活 1 行 → 修复后只删真正的测试行."""
        kept, meta = self._run(self.HEADERS, self.BLOOD_ROWS)
        kept_ids = [r["order_id"] for r in kept]
        self.assertEqual(kept_ids, ["1", "2", "3", "4", "6"],
                         f"真实业务行被当测试数据删除: {meta}")
        self.assertEqual(meta["rows_after"], 5)
        self.assertEqual(meta["removed_rows"], [6])  # order_id=5 → 第 6 行

    def test_substring_only_matches_are_kept(self):
        """逐个反例: 子串命中但非独立单词的一律保留."""
        for value in ("BARCELONA", "Latest", "Protest", "attestation",
                      "Greatest", "TEMPE", "template", "Barbados", "foobar"):
            kept, _ = self._run(["v"], [{"v": value}])
            self.assertEqual(len(kept), 1, f"{value!r} 被误删（子串匹配回归）")

    def test_real_test_rows_still_removed(self):
        """正向: 独立单词 / CJK 关键词仍然删除（守卫不得把功能删空）."""
        for value in ("test", "TEST DATA", "please ignore, debug row",
                      "foo", "bar", "tmp file", "这是测试数据", "请忽略此行"):
            kept, _ = self._run(["v"], [{"v": value}])
            self.assertEqual(len(kept), 0, f"{value!r} 应被识别为测试数据")

    def test_ascii_keyword_adjacent_to_cjk_still_matches(self):
        """CJK 邻接不得挡住 ASCII 词边界（\\b 在 CJK 旁不成立, 故用显式字符类）."""
        kept, _ = self._run(["v"], [{"v": "订单bar"}])
        self.assertEqual(len(kept), 0)

    def test_removal_is_auditable(self):
        """删除必须可审计: 命中关键词 + 列 + 行号（此前只有行号）."""
        _, meta = self._run(self.HEADERS, self.BLOOD_ROWS)
        self.assertEqual(meta["keyword_hits"], {"test": 1})
        self.assertEqual(meta["removed_details"],
                         [{"row": 6, "column": "customer", "keyword": "test"}])

    def test_empty_input_no_crash(self):
        kept, meta = self._run(self.HEADERS, [])
        self.assertEqual(kept, [])
        self.assertEqual(meta["rows_before"], 0)
        self.assertEqual(meta["keyword_hits"], {})


# ── DC-2: fix_dates 行号 ──────────────────────────────────────────────

class TestFixDatesRowNumbers(unittest.TestCase):
    """DC-2: rows.index(r) 对重复行返回首个下标."""

    def test_duplicate_rows_report_distinct_row_numbers(self):
        rows = [
            {"date": "2026-01-05", "note": "ok"},
            {"date": "NOT A DATE", "note": "dup"},   # 第 3 行
            {"date": "2026-02-06", "note": "ok"},
            {"date": "NOT A DATE", "note": "dup"},   # 第 5 行, 与第 3 行逐字相同
        ]
        _, meta = dc.op_fix_dates(["date", "note"], rows, ["date"])
        self.assertEqual([u["row"] for u in meta["unfixable"]], [3, 5],
                         "重复行让 rows.index 报同一行号两次, 真坏行永不出现")

    def test_unfixable_row_numbers_are_1_based_with_header(self):
        rows = [{"date": "2026-01-01"}, {"date": "garbage"}]
        _, meta = dc.op_fix_dates(["date"], rows, ["date"])
        self.assertEqual(meta["unfixable"][0]["row"], 3)


# ── DC-3: 不存在的列诚实上报 ──────────────────────────────────────────

class TestMissingColumnsHonesty(unittest.TestCase):
    """DC-3: 列名不存在（LLM 大小写/猜名）曾静默 0 改动 → 渲染成成功."""

    ROWS = [{"status": "ACTIVE", "name": "a"}, {"status": "Closed", "name": "b"}]

    def test_fix_case_all_columns_missing_marks_skipped(self):
        _, meta = dc.op_fix_case(["status", "name"], [dict(r) for r in self.ROWS],
                                 ["Status"])
        self.assertIn("skipped", meta, "全部目标列不存在必须标 skipped（渲染 ⚠️）")
        self.assertEqual(meta["missing_columns"], ["Status"])

    def test_fix_case_partial_missing_still_applies_existing(self):
        rows = [dict(r) for r in self.ROWS]
        _, meta = dc.op_fix_case(["status", "name"], rows, ["status", "Statuz"])
        self.assertNotIn("skipped", meta)          # 有列真的改了, 不算整步跳过
        self.assertEqual(meta["missing_columns"], ["Statuz"])
        self.assertEqual(meta["columns"], ["status"])
        self.assertEqual(rows[0]["status"], "active")

    def test_fix_dates_all_columns_missing_marks_skipped(self):
        _, meta = dc.op_fix_dates(["status", "name"], [dict(r) for r in self.ROWS],
                                  ["order_date"])
        self.assertIn("skipped", meta)
        self.assertEqual(meta["missing_columns"], ["order_date"])

    def test_fix_dates_auto_detect_never_reports_missing(self):
        """未传列名时自动检测出的列必然存在, 不得凭空产生 missing_columns."""
        rows = [{"d": "2026/01/02"}, {"d": "2026/03/04"}]
        _, meta = dc.op_fix_dates(["d"], rows, [])
        self.assertNotIn("missing_columns", meta)
        self.assertNotIn("skipped", meta)


# ── DC-4: dedup_near 披露假定 ID 列 ──────────────────────────────────

class TestDedupNearDisclosesIdColumn(unittest.TestCase):
    """DC-4: 首列非 ID 的表会删掉真实订单, 结果里却看不出键了哪一列."""

    def test_id_column_is_reported(self):
        headers = ["ship_date", "customer", "amount"]
        rows = [
            {"ship_date": "2026-01-01", "customer": "ACME", "amount": "100"},
            {"ship_date": "2026-02-01", "customer": "ACME", "amount": "100"},
        ]
        kept, meta = dc.op_dedup_near(headers, rows, [])
        self.assertEqual(meta["id_column"], "ship_date")
        self.assertEqual(meta["rows_removed"], 1)   # 行为不变, 只是可见
        self.assertEqual(len(kept), 1)

    def test_too_few_columns_still_skipped(self):
        _, meta = dc.op_dedup_near(["only"], [{"only": "x"}], [])
        self.assertIn("skipped", meta)


# ── 渲染层: 新证据必须真的到达用户 ────────────────────────────────────

class TestProxyRendersNewEvidence(unittest.TestCase):
    """新增的可审计字段若不渲染 = 白加（V37.9.293 生产端-消费端断链教训）."""

    def test_missing_columns_render_as_issue(self):
        r = _fmt_execute({
            "input": "a.csv", "original_rows": 10, "final_rows": 10,
            "output": "a_cleaned.csv",
            "steps": [{"operation": "fix_case", "cells_changed": 0,
                       "columns": ["status"], "missing_columns": ["Statuz"]}]})
        self.assertIn("Statuz", r)
        self.assertIn("列不存在", r)
        self.assertFalse(r.startswith("✅"), "有列不存在时 header 不得全绿")

    def test_remove_test_keyword_hits_rendered(self):
        r = _fmt_execute({
            "input": "a.csv", "original_rows": 10, "final_rows": 4,
            "output": "a_cleaned.csv",
            "steps": [{"operation": "remove_test", "rows_removed": 6,
                       "keyword_hits": {"bar": 4, "test": 2}}]})
        self.assertIn("bar×4", r)
        self.assertIn("test×2", r)

    def test_dedup_near_id_column_rendered(self):
        r = _fmt_execute({
            "input": "a.csv", "original_rows": 10, "final_rows": 9,
            "output": "a_cleaned.csv",
            "steps": [{"operation": "dedup_near", "rows_removed": 1,
                       "id_column": "ship_date"}]})
        self.assertIn("ship_date", r)

    def test_row_number_caveat_not_rendered_as_noise(self):
        """DC-5 标注只进 JSON/report.md（proxy 本就不显示行号）, 不制造噪声."""
        r = _fmt_execute({
            "input": "a.csv", "original_rows": 10, "final_rows": 9,
            "output": "a_cleaned.csv",
            "steps": [{"operation": "fix_dates", "dates_fixed": 1,
                       "columns": ["d"], "unfixable": [{"row": 3}],
                       "row_numbers_relative_to": "本步输入（前序操作已删除 1 行）"}]})
        self.assertNotIn("row_numbers_relative_to", r)
        self.assertIn("1 个值无法解析", r)   # 既有证据仍在

    def test_clean_run_still_green(self):
        """回归: 无新证据时仍是全绿 ✅（不得制造噪声）."""
        r = _fmt_execute({
            "input": "a.csv", "original_rows": 10, "final_rows": 9,
            "output": "a_cleaned.csv",
            "steps": [{"operation": "trim", "cells_trimmed": 3},
                      {"operation": "dedup", "rows_removed": 1}]})
        self.assertTrue(r.startswith("✅"))


# ── DC-5: 跨步骤行号语义诚实标注 ─────────────────────────────────────

class TestRowNumberCaveat(unittest.TestCase):
    """DC-5: 前序操作删行后行号语义漂移（默认链 trim,dedup,fix_dates 即触发）."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = (dc.WORKSPACE, dc.VERSION_DIR, dc.LOG_FILE)
        dc.WORKSPACE = self.tmp
        dc.VERSION_DIR = os.path.join(self.tmp, "versions")
        dc.LOG_FILE = os.path.join(self.tmp, "audit.jsonl")
        os.makedirs(dc.VERSION_DIR, exist_ok=True)
        self.csv = os.path.join(self.tmp, "t.csv")
        with open(self.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["id", "date"])
            w.writeheader()
            w.writerow({"id": "1", "date": "2026-01-01"})
            w.writerow({"id": "1", "date": "2026-01-01"})   # 原文件第 3 行, 被 dedup 删
            w.writerow({"id": "2", "date": "BAD"})          # 原文件第 4 行 = 真坏行
            w.writerow({"id": "3", "date": "2026-02-02"})
            w.writerow({"id": "4", "date": "2026-03-03"})

    def tearDown(self):
        dc.WORKSPACE, dc.VERSION_DIR, dc.LOG_FILE = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _steps(self, ops):
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = dc.cmd_execute(self.csv, ops)
        finally:
            sys.stdout = old
        self.assertEqual(rc, 0)
        return json.loads(buf.getvalue())["steps"]

    def test_caveat_present_when_prior_op_removed_rows(self):
        steps = self._steps(["dedup", "fix_dates"])
        fd = [s for s in steps if s["operation"] == "fix_dates"][0]
        self.assertEqual([u["row"] for u in fd["unfixable"]], [3])   # 本步输入位置
        # 原文件里这条坏日期在第 4 行 —— 报告写 3 = DC-5 语义漂移
        self.assertIn("row_numbers_relative_to", fd,
                      "行号已相对本步输入却无任何标注 = 用户按行号去改会改错行")
        self.assertIn("1", fd["row_numbers_relative_to"])

    def test_no_caveat_when_row_count_unchanged(self):
        """回归: 没有前序删行时不得凭空加标注（噪声）."""
        steps = self._steps(["fix_dates"])
        fd = [s for s in steps if s["operation"] == "fix_dates"][0]
        self.assertEqual([u["row"] for u in fd["unfixable"]], [4])   # 与原文件一致
        self.assertNotIn("row_numbers_relative_to", fd)


# ── 源码守卫: 反模式退役 ──────────────────────────────────────────────

class TestSourceGuards(unittest.TestCase):

    def setUp(self):
        self.src = _read(_DATA_CLEAN)

    def test_marker(self):
        self.assertIn("V37.9.316", self.src)

    def test_rows_index_antipattern_retired(self):
        # 只扫可执行行: 血案注释本身含 "rows.index(r)" 字样, 裸 assertNotIn 会被
        # 自己的注释咬（V37.9.178 家族, 首次编写本守卫时当场复现）。
        code = [ln for ln in self.src.splitlines()
                if not ln.lstrip().startswith("#")]
        hits = [ln.strip() for ln in code if "rows.index(" in ln]
        self.assertEqual(hits, [],
                         "rows.index(r) 对重复行返回首个下标 —— 已退役, 用 enumerate")
        # 防空转: 替代实现必须真的在 op_fix_dates 里
        self.assertIn("for idx, r in enumerate(rows):", self.src)

    def test_keyword_tables_split_ascii_and_cjk(self):
        self.assertTrue(hasattr(dc, "_TEST_KEYWORDS_ASCII"))
        self.assertTrue(hasattr(dc, "_TEST_KEYWORDS_CJK"))
        # 防空转: 表非空且各自内容正确分类
        self.assertGreaterEqual(len(dc._TEST_KEYWORDS_ASCII), 5)
        self.assertTrue(all(k.isascii() for k in dc._TEST_KEYWORDS_ASCII))
        self.assertTrue(all(not k.isascii() for k in dc._TEST_KEYWORDS_CJK))

    def test_ascii_keywords_compiled_with_boundaries(self):
        """防空转: 每个 ASCII 关键词都有词边界正则, 不是裸子串."""
        self.assertEqual(set(dc._TEST_KEYWORD_RE), set(dc._TEST_KEYWORDS_ASCII))
        for kw, rx in dc._TEST_KEYWORD_RE.items():
            self.assertTrue(rx.search(kw), f"{kw} 自身应匹配")
            self.assertFalse(rx.search("x" + kw + "x"), f"{kw} 词边界失效")


if __name__ == "__main__":
    unittest.main(verbosity=2)
