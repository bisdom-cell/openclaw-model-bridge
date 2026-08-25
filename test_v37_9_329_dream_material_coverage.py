#!/usr/bin/env python3
"""
V37.9.329 守卫 — Dream 素材覆盖面：位置性窗口 → 内容感知取样 + 跨夜轮转

血案谱系（同一个病的三次现形）：
  1. V37.9.326 之前：sources 取归档**头** 15K → 永远是建库最早的 2026-04
     → 用户报告「梦境结论几乎只引用 4 月的观点和数据」
  2. V37.9.326：改取**尾** 15K → 永远是最近几天
     → 用户 2026-08-25 看产品：「确实是几乎都是 8 月」= 换了个方向钉死
  3. 同日审计发现第二个钉死点（V37.9.326 漏修）：MAP_SIGNALS 按 find|sort =
     **字母序**拼接后 `utf8_truncate 14000` 保头截断。2026-08-25 生产实测
     17078 chars > 14000 → openreview_top / pwc_daily / rss_blogs /
     **semantic_scholar_daily** 四个源整块从未进过 Reduce 的推理材料，
     而梦境页脚照写「19 sources deep-analyzed」= fail-plausible。
     决定谁被思考的是文件名首字母。

架构约束（用户 2026-08-25 追加）：「后续每天都要新增的数据，需要在架构上
充分考虑未来的数据增长」。量化现状：notes 274 个信号块中预算只放得下 ~40 个
（15%），KB 48MB 且每日增长 → **月份分层只修「哪 15%」，修不了「15% 会变
8% 会变 3%」**。答案是把覆盖率从空间维度换到时间维度：日序轮转偏移让不同夜
取到不同切片，语料翻倍 → 覆盖一轮的周期翻倍，而非覆盖率减半。
"""
import os
import re
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

DREAM_SH = os.path.join(REPO, "kb_dream.sh")

from kb_dream_helpers import (  # noqa: E402
    budget_source_blocks,
    month_round_robin,
    month_stratified_sections,
)


def _strip_sh_comments(text):
    """剥 bash 注释行 —— 本次修复的注释里逐字写着被退役的字面量
    (utf8_tail_truncate 15000 / utf8_truncate 14000 / 90)，不剥会被自己咬
    (V37.9.178 家族)。"""
    return "\n".join(
        ln for ln in text.splitlines() if not ln.lstrip().startswith("#")
    )


def _dream_src(executable_only=False):
    with open(DREAM_SH, "r") as f:
        src = f.read()
    return _strip_sh_comments(src) if executable_only else src


def _archive(months=(4, 5, 6, 7, 8), days=20):
    """复刻 kb_append_source.sh 产出的编年体归档：每天一个 ## YYYY-MM-DD 段。"""
    return "# 归档标题\n\n" + "".join(
        f"## 2026-{m:02d}-{d:02d}\n- 条目 {m:02d}-{d:02d}\n- 另一条 {m:02d}-{d:02d}\n\n"
        for m in months
        for d in range(1, days + 1)
    )


def _months_in(text):
    return sorted(set(re.findall(r"## (2026-\d\d)", text)))


def _sections_in(text):
    return re.findall(r"## (2026-\d\d-\d\d)", text)


# 2026-08-25 生产实测的每源信号字符数（含 "## name\n" 头）
PROD_SOURCES = [
    ("acl_anthology", 747), ("ai_leaders_blogs", 1054), ("ai_leaders_bsky", 1053),
    ("ai_leaders_x", 1120), ("arxiv_daily", 1065), ("chaspark", 842),
    ("dblp_daily", 731), ("finance_daily", 634), ("freight_daily", 793),
    ("github_trending", 954), ("hf_papers_daily", 775), ("hn_daily", 702),
    ("karpathy_x", 987), ("ontology_sources", 1470), ("openclaw_official", 911),
    ("openreview_top", 684), ("pwc_daily", 733), ("rss_blogs", 755),
    ("semantic_scholar_daily", 1068),
]


def _prod_blocks():
    return "".join(
        f"## {n}\n" + "信" * (c - len(n) - 4) + "\n" for n, c in PROD_SOURCES
    )


class TestMonthStratifiedSections(unittest.TestCase):
    """sources 每源窗口：位置性 → 内容感知。"""

    def test_blood_lesson_both_positional_windows_pin(self):
        """先证明血案前提成立：头窗口只出最老月、尾窗口只出最新月。

        防空转 —— 若这条不成立，下面的「分层横跨」断言就没有对照意义。
        """
        arch = _archive()
        head, tail = _months_in(arch[:800]), _months_in(arch[-800:])
        self.assertNotIn("2026-08", head, f"头部窗口竟能看到最新月: {head}")
        self.assertNotIn("2026-04", tail, f"尾部窗口竟能看到最老月: {tail}")
        self.assertLess(len(head), 5, f"头部窗口竟横跨全部月份: {head}")
        self.assertLess(len(tail), 5, f"尾部窗口竟横跨全部月份: {tail}")

    def test_stratified_spans_all_months(self):
        out = month_stratified_sections(_archive(), 800)
        self.assertEqual(_months_in(out),
                         ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08"],
                         f"分层取样未横跨全部月份: {_months_in(out)}")

    def test_budget_respected(self):
        for budget in (400, 800, 3000, 100000):
            out = month_stratified_sections(_archive(), budget)
            self.assertLessEqual(len(out), budget, f"预算 {budget} 被突破")

    def test_offset_rotates_to_different_sections(self):
        """跨夜覆盖：不同 offset 取到不同段（增长维度的核心机制）。"""
        arch = _archive()
        a = _sections_in(month_stratified_sections(arch, 400, offset=0))
        b = _sections_in(month_stratified_sections(arch, 400, offset=1))
        self.assertTrue(a and b, "取样为空")
        self.assertNotEqual(a, b, "offset 未改变取样内容 —— 跨夜覆盖失效")

    def test_growth_property_cumulative_coverage(self):
        """🔴 架构性质：预算固定、语料增长时，累计覆盖率仍为 100%。

        20 个段、每夜预算只够 ~5 段 → 连续 20 夜的并集必须覆盖全部段。
        这是「覆盖率从空间维度换到时间维度」的可执行断言。
        """
        arch = _archive(months=(8,), days=20)
        all_secs = set(_sections_in(arch))
        per_night = len(_sections_in(month_stratified_sections(arch, 300, offset=0)))
        self.assertLess(per_night, len(all_secs),
                        "预算太大导致一夜就全覆盖，本测试失去意义")
        seen = set()
        for night in range(len(all_secs)):
            seen |= set(_sections_in(month_stratified_sections(arch, 300, offset=night)))
        self.assertEqual(seen, all_secs,
                         f"{len(all_secs)} 夜轮转后仍有 {all_secs - seen} 从未被看到")

    def test_fail_open_no_date_sections(self):
        """格式漂移/非编年体源 → 回退 V37.9.326 尾部窗口，绝不返回空。"""
        plain = "x" * 2000
        self.assertEqual(month_stratified_sections(plain, 500), plain[-500:])

    def test_single_oversized_section_never_empty(self):
        huge = "## 2026-08-01\n" + "内" * 5000
        out = month_stratified_sections(huge, 200)
        self.assertTrue(out, "单段超预算时返回空 —— 违反 MR-4")
        self.assertLessEqual(len(out), 200)

    def test_edge_inputs_do_not_crash(self):
        self.assertEqual(month_stratified_sections("", 100), "")
        self.assertEqual(month_stratified_sections(None, 100), "")
        self.assertEqual(month_stratified_sections("abc", 0), "")


class TestBudgetSourceBlocks(unittest.TestCase):
    """跨源预算：字母序保头 → max-min 公平分配。"""

    def test_blood_lesson_alphabetical_head_truncate_drops_sources(self):
        """防空转 + 血案前提：生产真实量级下旧行为确实整块丢源。"""
        blocks = _prod_blocks()
        self.assertGreater(len(blocks), 14000,
                           "fixture 未超预算 → 旧行为不会截断，对照失效")
        old = blocks[:14000]
        dropped = [n for n, _ in PROD_SOURCES if f"## {n}" not in old]
        self.assertTrue(dropped, "旧行为竟未丢源，血案前提失效")
        self.assertIn("semantic_scholar_daily", dropped,
                      "字母序末尾的高价值源竟未被丢弃，fixture 与生产不符")

    def test_every_source_present_after_fix(self):
        out = budget_source_blocks(_prod_blocks(), 14000)
        missing = [n for n, _ in PROD_SOURCES if f"## {n}" not in out]
        self.assertEqual(missing, [], f"仍有源整块缺席: {missing}")

    def test_budget_respected_including_separators(self):
        """分隔符必须计入预算（首版实测 14018 > 14000）。"""
        blocks = _prod_blocks()
        for budget in (5000, 14000, 28000):
            out = budget_source_blocks(blocks, budget)
            self.assertLessEqual(len(out), budget, f"预算 {budget} 被突破: {len(out)}")

    def test_under_budget_passthrough_byte_identical(self):
        blocks = _prod_blocks()
        self.assertEqual(budget_source_blocks(blocks, len(blocks) + 1), blocks)

    def test_trimming_falls_on_longest_blocks(self):
        out = budget_source_blocks(_prod_blocks(), 14000)
        sizes = [len(b) for b in out.split("## ")[1:]]
        self.assertTrue(sizes)
        self.assertLess(max(sizes) - min(sizes), 300,
                        "分配不均 —— max-min 公平性失效")

    def test_growth_more_sources_still_all_present(self):
        """源数量增长（19 → 40）时仍然人人在场。"""
        blocks = "".join(f"## src_{i:02d}\n" + "信" * 900 + "\n" for i in range(40))
        out = budget_source_blocks(blocks, 28000)
        present = sum(1 for i in range(40) if f"## src_{i:02d}" in out)
        self.assertEqual(present, 40, f"源增长后只剩 {present}/40 在场")

    def test_degenerate_budget_keeps_headers(self):
        """预算小到放不下表头时，优先保证在场（静默丢源比略超预算更糟）。"""
        blocks = _prod_blocks()
        out = budget_source_blocks(blocks, 100)
        present = sum(1 for n, _ in PROD_SOURCES if f"## {n}" in out)
        self.assertEqual(present, len(PROD_SOURCES))


class TestMonthRoundRobinOffset(unittest.TestCase):
    """notes 侧轮转加 offset —— 默认 0 必须与 V37.9.326 逐字节相同。"""

    PATHS = [f"2026{m:02d}{d:02d}0000.md" for m in (4, 5, 6, 7, 8) for d in (1, 2, 3)]

    def test_offset_zero_is_backward_compatible(self):
        self.assertEqual(month_round_robin(self.PATHS),
                         month_round_robin(self.PATHS, offset=0))
        self.assertEqual(month_round_robin(self.PATHS)[:5],
                         ["20260803" + "0000.md", "202607030000.md",
                          "202606030000.md", "202605030000.md", "202604030000.md"])

    def test_rotation_is_a_permutation(self):
        for off in (0, 1, 2, 7, 366):
            self.assertEqual(sorted(month_round_robin(self.PATHS, offset=off)),
                             sorted(self.PATHS), f"offset={off} 丢失或重复了条目")

    def test_offset_changes_head_of_window(self):
        a = month_round_robin(self.PATHS, offset=0)[:5]
        b = month_round_robin(self.PATHS, offset=1)[:5]
        self.assertNotEqual(a, b)

    def test_cross_night_covers_whole_corpus(self):
        """3 夜 × 前 5 名 = 15/15 全覆盖（增长维度实证）。"""
        seen = set()
        for off in range(3):
            seen |= set(month_round_robin(self.PATHS, offset=off)[:5])
        self.assertEqual(seen, set(self.PATHS))

    def test_invalid_offset_degrades_to_zero(self):
        self.assertEqual(month_round_robin(self.PATHS, offset="x"),
                         month_round_robin(self.PATHS))


class TestDreamWiringSourceGuards(unittest.TestCase):
    """kb_dream.sh 接线守卫（全部只看可执行行，注释里有被退役的字面量）。"""

    def setUp(self):
        self.src = _dream_src(executable_only=True)
        self.raw = _dream_src()

    def test_not_vacuous(self):
        self.assertGreater(len(self.src), 20000, "剥注释后源码过短，守卫会假通过")
        self.assertIn("kb_dream", self.raw)

    def test_rotation_offset_defined_base10(self):
        """date +%j 的 '093' 直接进 python 源码会被当非法八进制字面量。"""
        self.assertRegex(self.src, r"ROTATION_OFFSET=\$\(\(10#\$\(.*date \+%j\)\)\)")

    ENV_OFFSET = "int(os.environ.get('ROTATION_OFFSET') or 0)"

    def test_stratified_wired_with_offset(self):
        self.assertIn(f"month_stratified_sections(text, 24000, offset={self.ENV_OFFSET})",
                      self.src)

    def test_notes_rotation_wired_with_offset(self):
        self.assertIn(f"month_round_robin(paths, offset={self.ENV_OFFSET})", self.src)

    def test_offset_never_interpolated_into_python_source(self):
        """🔴 偏移必须经 env 读取，不得插值进 python 源码。

        插值时若变量为空会渲染成 `offset=)` → SyntaxError → 整个 python 编译失败：
        notes 侧有 `[ -z ]` 兜底，sources 侧会拿到**空 full_content 去喂 LLM**。
        这个洞是 test_v37_9_326 的 E2E 提取块在隔离环境跑时当场抓到的。
        """
        self.assertNotIn("offset=$ROTATION_OFFSET", self.src,
                         "偏移仍在做 python 源码插值（空值 → SyntaxError）")
        self.assertIn("export ROTATION_OFFSET", self.src)
        self.assertIn("ROTATION_OFFSET=${ROTATION_OFFSET:-0}", self.src)

    def test_sources_empty_output_falls_back(self):
        """python 整体失败时 full_content 为空 —— 绝不拿空内容喂 LLM。"""
        self.assertIn('if [ -z "${full_content// }" ]; then', self.src)
        self.assertIn("utf8_tail_truncate 24000", self.src)

    def test_budget_helper_wired(self):
        self.assertIn("budget_source_blocks(text, 28000)", self.src)

    def test_positional_window_antipatterns_retired(self):
        """三个被退役的位置性窗口不得在可执行行里复活。"""
        self.assertNotIn("utf8_tail_truncate 15000", self.src,
                         "sources 尾部窗口未退役（8 月钉死）")
        self.assertNotIn("utf8_truncate 14000", self.src,
                         "sources 字母序保头截断未退役（静默丢源）")
        self.assertNotIn("utf8_truncate 13000", self.src)
        self.assertNotIn("utf8_truncate 30000", self.src)

    def test_budgets_raised(self):
        self.assertIn("utf8_truncate 60000", self.src)
        self.assertIn("utf8_truncate 28000", self.src)   # notes（输入已是轮转序，保头无害）
        self.assertIn("utf8_truncate 34000", self.src)   # 降级采样分支对称放大

    def test_timeouts_raised(self):
        """窗口放大必须同步放宽单次调用超时（Map 的 90s 已实录 curl(28)）。"""
        self.assertNotIn("1200 0.5 90", self.src, "Map 超时仍是 90s")
        self.assertEqual(self.src.count("1200 0.5 150"), 2, "两个 Map 站点须一致")
        self.assertNotIn("5000 0.85 300", self.src)
        self.assertNotIn("6000 0.8 300", self.src)
        self.assertIn("5000 0.85 420", self.src)
        self.assertIn("6000 0.8 420", self.src)

    def test_prompt_hash_bumped_and_consistent(self):
        """两个站点必须同为 v5（窗口语义变更须使旧缓存失效；MR-8 一物一形）。"""
        hashes = re.findall(r'prompt_hash="(v\d+)"', self.src)
        self.assertEqual(len(hashes), 2, f"prompt_hash 站点数变了: {hashes}")
        self.assertEqual(set(hashes), {"v5"}, f"两站点不一致或未 bump: {hashes}")

    def test_fail_open_paths_present(self):
        self.assertIn("month_stratified_sections 失败, 回退尾部窗口", self.raw)
        self.assertIn("budget_source_blocks 失败, 回退保头截断", self.raw)
        self.assertIn("month_round_robin 失败, 回退原序", self.raw)

    def test_shell_syntax(self):
        r = subprocess.run(["bash", "-n", DREAM_SH], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestTimeContrastPrompts(unittest.TestCase):
    """B：让 prompt 显式做跨时间对照，且不得因此鼓励编造。"""

    def setUp(self):
        self.raw = _dream_src()

    def test_deep_requires_cross_month_chain(self):
        self.assertIn("V37.9.329 跨时间对照", self.raw)
        self.assertIn("至少 1 条必须是**跨时间**关联", self.raw)

    def test_deep_has_honest_escape_hatch(self):
        """找不到跨月证据链时必须显式说找不到 —— 否则新规则会逼出编造。"""
        self.assertIn("未发现跨月印证/反转链", self.raw)

    def test_both_prompts_forbid_cross_month_conflation(self):
        self.assertEqual(
            self.raw.count("禁止把不同月份的信号当作同期事件并列"), 2,
            "DEEP 与 WIDE_RADAR 都必须有「不同月份 ≠ 同期事件」禁令")

    def test_wide_radar_labels_slow_burn(self):
        self.assertIn("慢烧: X 月起反复出现", self.raw)
        self.assertIn("(新出现)", self.raw)

    def test_existing_guards_preserved(self):
        """新增条款不得挤掉既有防线。"""
        self.assertIn("V37.9.68 主题去重硬规则", self.raw)
        # 主 prompt + retry prompt 各有一份，故用下界而非精确计数（加 retry 不该破坏守卫）
        self.assertGreaterEqual(self.raw.count("V37.8.6 反污染"), 2)
        self.assertGreaterEqual(self.raw.count("V37.9.235 信号时效标注"), 2)
        self.assertGreaterEqual(self.raw.count("${DREAM_HG_GUARD}${DREAM_CREDIBILITY}"), 4)

    def test_material_headers_declare_stratification(self):
        """素材段头要告诉 LLM 这批材料是分层取样的（上下文工程，原则 #12）。"""
        self.assertIn("月份分层取样", self.raw)
        self.assertIn("起点按日序轮转", self.raw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
