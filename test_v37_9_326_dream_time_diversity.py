#!/usr/bin/env python3
"""
test_v37_9_326_dream_time_diversity.py — V37.9.326 守卫

血案（用户报告）：每天凌晨 Agent Dream 的结论几乎都在引用 2026-04 的观点和数据，
其他月份很少出现。grounded 根因是三个相互叠加的「素材窗口钉死在 4 月」：

  1. sources Map 输入 = `cat 归档 | utf8_truncate 15000`（保头）——而 sources 归档
     是 kb_append_source.sh append-to-end 的编年体文件，头 15K = 建库最早（4 月）
     条目，归档只在尾部生长 → 窗口永不移动；且 cache key 含 file_size（天天变）
     → 每晚 cache miss → 对一模一样的 4 月头部重复烧 14 次 LLM 提取。
  2. Reduce cache-load 按 `find | sort`（文件名时间戳升序 = 最老在前）遍历笔记
     → 信号块最老在前进入 NOTES_SIGNALS。
  3. REDUCE_MULTI_MATERIAL = 全局 utf8_truncate 30000 **保头截尾**：preamble +
     sources 信号（~15-20K，全 4 月味）之后，笔记信号只剩头几块（也是最老的）。
     5-8 月的一切都在 30K 之外被截掉。

佐证：降级采样分支自己写着 [起源]头/[历史]中/[最新]尾（tail -100）—— 写降级分支
的人知道要时间多样性；每晚真跑的主路径反而是 head-15K + 升序。最常走的路最少被审。

修复：sources 输入改尾部窗口（utf8_tail_truncate + prompt_hash v4）/ 笔记改月份
轮转序（month_round_robin，近月先、各月混合，FAIL-OPEN 回退原序）/ 每部分独立
预算（sources ≤14K / notes ≤13K）防 sources 把 notes 挤出窗口 / 降级分支对称修。
"""
import os
import re
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

from kb_dream_helpers import month_round_robin  # noqa: E402


def _read(name):
    with open(os.path.join(REPO, name), encoding="utf-8") as f:
        return f.read()


def _code_lines(src):
    """剥 # 注释行（V37.9.178 家族：血案注释里逐字写着被退役的形态）。"""
    return "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))


# ════════════════════════════════════════════════════════════════════
# 1. month_round_robin 纯函数
# ════════════════════════════════════════════════════════════════════
class TestMonthRoundRobin(unittest.TestCase):
    @staticmethod
    def _mk(months, per_month=3):
        """构造升序（最老在前）路径列表，模拟 find|sort 的原始序。"""
        paths = []
        for mo in months:
            for d in range(1, per_month + 1):
                paths.append(f"/x/notes/{mo}{d:02d}120000.md")
        return sorted(paths)

    def test_blood_case_head_window_spans_all_months(self):
        """血案回归：升序原序的头部窗口只有最老月；轮转序的头部窗口横跨全部月份。"""
        months = ["202604", "202605", "202606", "202607", "202608"]
        paths = self._mk(months, per_month=10)
        k = len(months)
        old_head = {p.split("/")[-1][:6] for p in sorted(paths)[:k]}
        self.assertEqual(old_head, {"202604"}, "前提：旧序头部确实钉死在最老月（防守卫空转）")
        new_head = {p.split("/")[-1][:6] for p in month_round_robin(paths)[:k]}
        self.assertEqual(new_head, set(months), "轮转序头 K 项必须覆盖全部月份")

    def test_newest_month_and_newest_note_first(self):
        paths = self._mk(["202604", "202608"], per_month=3)
        out = month_round_robin(paths)
        self.assertTrue(out[0].startswith("/x/notes/20260803"), "开局 = 最新月的最新笔记")
        self.assertTrue(out[1].startswith("/x/notes/20260403"), "第二件 = 次新月的最新笔记")

    def test_no_loss_no_dup(self):
        paths = self._mk(["202604", "202607"], per_month=5) + ["/x/notes/weird-name.md"]
        out = month_round_robin(paths)
        self.assertEqual(sorted(out), sorted(paths), "输入输出必须一一对应（MR-4 零丢失）")

    def test_invalid_names_preserved_at_tail(self):
        paths = ["/x/notes/20260801120000.md", "/x/notes/README.md", "/x/notes/20269901.md"]
        out = month_round_robin(paths)
        self.assertEqual(out[0], "/x/notes/20260801120000.md")
        self.assertEqual(set(out[1:]), {"/x/notes/README.md", "/x/notes/20269901.md"},
                         "无法解析月份的（含月份 99 非法）保守落尾不丢失")

    def test_uneven_buckets_drain_completely(self):
        paths = self._mk(["202608"], per_month=1) + self._mk(["202604"], per_month=4)
        out = month_round_robin(paths)
        self.assertEqual(len(out), 5)
        self.assertTrue(out[0].startswith("/x/notes/202608"))
        self.assertTrue(all(p.startswith("/x/notes/202604") for p in out[2:]),
                        "短桶取空后长桶继续排空")

    def test_empty_input(self):
        self.assertEqual(month_round_robin([]), [])


# ════════════════════════════════════════════════════════════════════
# 2. utf8_tail_truncate（bash 行为级：从源码抽真函数跑）
# ════════════════════════════════════════════════════════════════════
class TestUtf8TailTruncate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        m = re.search(r"(utf8_tail_truncate\(\) \{.*?\n\})", _read("kb_dream.sh"), re.S)
        assert m, "防空转：未能从 kb_dream.sh 抽出 utf8_tail_truncate"
        cls.fn = m.group(1)

    def _run(self, text, n):
        r = subprocess.run(["bash", "-c", self.fn + f"\nutf8_tail_truncate {n}"],
                           input=text, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_keeps_tail_drops_head(self):
        text = "OLD-APRIL-MARKER\n" + ("x" * 80 + "\n") * 200 + "NEW-RECENT-MARKER\n"
        out = self._run(text, 2000)
        self.assertIn("NEW-RECENT-MARKER", out, "尾部窗口必须保住最新内容")
        self.assertNotIn("OLD-APRIL-MARKER", out, "头部（最老）内容必须被裁掉")
        self.assertLessEqual(len(out), 2010)

    def test_short_text_unchanged(self):
        self.assertEqual(self._run("短文本中文测试\n", 2000).strip(), "短文本中文测试")

    def test_cjk_safe(self):
        text = ("中文段落测试" * 50 + "\n") * 100 + "尾部锚点\n"
        out = self._run(text, 3000)
        self.assertIn("尾部锚点", out)


# ════════════════════════════════════════════════════════════════════
# 3. sources Map 窗口 + cache 版本（源码守卫，剥注释）
# ════════════════════════════════════════════════════════════════════
class TestSourcesMapWindow(unittest.TestCase):
    def setUp(self):
        self.code = _code_lines(_read("kb_dream.sh"))

    def test_map_input_is_not_positional_window(self):
        """V37.9.329 pin 演进：原断言「必须是尾部窗口」——而尾部窗口正是被修掉的东西。

        意图升级而非削弱：本测试原本守的是「不得是保头窗口」（头 15K = 永远的 4 月），
        现在守更强的性质——**两种位置性窗口都不用**（用户 2026-08-25 看产品发现尾部
        窗口把梦境钉死在 8 月），Map 输入必须走内容感知的月份分层取样。
        分层取样本身的行为由 test_v37_9_329_dream_material_coverage.py 覆盖。
        """
        self.assertNotIn('full_content=$(cat "$src" 2>/dev/null | utf8_truncate 15000)',
                         self.code, "头部窗口形态已退役（归档头 15K = 永远的 4 月）")
        self.assertNotIn('full_content=$(cat "$src" 2>/dev/null | utf8_tail_truncate 15000)',
                         self.code, "尾部窗口形态已退役（归档尾 = 永远的最近几天）")
        self.assertIn("month_stratified_sections", self.code,
                      "sources Map 输入必须走月份分层取样")

    def test_prompt_hash_both_sites_consistent(self):
        """MR-8：cache key 版本存在于 Map 写 + Reduce 读两站点，必须一致。

        V37.9.329 pin 演进：原断言硬编码 {"v4"}，而窗口语义每变一次就要 bump 一次
        （v3→v4→v5），值 pin 会让每次正当 bump 都撞红。本测试收敛为守**一致性**
        （两站点不同 = 缓存永远 miss 或读到错版本）；「必须 bump 到 v5」由
        test_v37_9_329_dream_material_coverage.py 精确 pin。
        """
        hashes = re.findall(r'prompt_hash="(v\d+)"', self.code)
        self.assertEqual(len(hashes), 2, "防空转：prompt_hash 应恰有 2 处（写/读）")
        self.assertEqual(len(set(hashes)), 1,
                         f"两站点 prompt_hash 不一致（缓存写/读会错版本）: {hashes}")


# ════════════════════════════════════════════════════════════════════
# 4. Reduce 组装：月份轮转 wiring + 每部分预算
# ════════════════════════════════════════════════════════════════════
class TestReduceAssembly(unittest.TestCase):
    def setUp(self):
        self.src = _read("kb_dream.sh")
        self.code = _code_lines(self.src)

    def test_cache_load_iterates_diverse_notes(self):
        self.assertIn('done <<< "$DIVERSE_NOTES"', self.code,
                      "cache-load 循环必须消费月份轮转序")
        self.assertIn("from kb_dream_helpers import month_round_robin", self.src)
        self.assertIn('[ -z "${DIVERSE_NOTES// }" ] && DIVERSE_NOTES="$ALL_NOTES"', self.code,
                      "FAIL-OPEN：helper 失败必须回退原序而非空素材")

    def test_map_write_loop_untouched(self):
        """Map 写路径保持 mtime 降序（预算受限时优先覆盖新笔记）——不受本次改动影响。"""
        self.assertIn('done <<< "$SORTED_NOTES"', self.code)

    def test_per_part_budgets(self):
        # V37.9.329 pin 演进：sources 侧退役 utf8_truncate（保头×字母序 = 静默丢源，
        # 2026-08-25 实测丢 4 个源），改 max-min 公平分配；预算 2x。意图「两类信号
        # 各有独立预算、都稳定在场」逐字保留且更强。
        self.assertNotIn('MAP_SIGNALS_BUDGETED=$(echo "$MAP_SIGNALS" | utf8_truncate 14000)',
                         self.code, "字母序保头截断已退役")
        self.assertIn("budget_source_blocks", self.code, "sources 侧必须走公平分配")
        self.assertIn('NOTES_SIGNALS_BUDGETED=$(echo "$NOTES_SIGNALS" | utf8_truncate 28000)',
                      self.code)
        m = re.search(r'REDUCE_DATA="\n# Phase 1a: 外部数据源信号\n(.*?)\n"\nfi', self.src, re.S)
        self.assertIsNotNone(m, "防空转：未抽到主分支 REDUCE_DATA")
        self.assertIn("$MAP_SIGNALS_BUDGETED", m.group(1), "主分支必须用预算后的 sources 信号")
        self.assertIn("$NOTES_SIGNALS_BUDGETED", m.group(1), "主分支必须用预算后的 notes 信号")

    def test_global_truncate_safety_net_retained(self):
        # V37.9.329: 30000 → 60000（上下文只用 ~10%，真实约束是单次调用延迟）。
        # 意图「全局安全网不得被移除」逐字保留。
        self.assertIn('REDUCE_MULTI_MATERIAL=$(echo "$REDUCE_DATA" | utf8_truncate 60000)',
                      self.code, "全局安全网不得被移除")

    def test_overview_header_declares_time_span(self):
        self.assertIn("月份轮转排列", self.src, "总览 header 必须向 LLM 声明素材的时间结构（原则 #12）")

    def test_sampling_branch_budget_symmetric_fix(self):
        """降级采样分支：sources 采样先预算 17K，防 notes 被全局保头截断整体挤出。"""
        # V37.9.329: 17000 → 34000，与主路径同比放大。
        self.assertIn('REDUCE_DATA=$(echo "$REDUCE_DATA" | utf8_truncate 34000)', self.code)

    def test_sampling_branch_time_diversity_intact(self):
        """既有降级分支的 起源/最新 结构是本次诊断的佐证——不得被顺手破坏。"""
        self.assertIn('tail_content=$(tail -100 "$src"', self.code)
        self.assertIn("[最新] $tail_content", self.src)


# ════════════════════════════════════════════════════════════════════
# 5. 端到端行为：真 bash 跑 DIVERSE_NOTES 赋值块
# ════════════════════════════════════════════════════════════════════
class TestDiverseNotesBlockE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        m = re.search(
            r'(DIVERSE_NOTES=\$\(echo "\$ALL_NOTES" \| python3 -c "\n.*?\n"\)\n'
            r'\s*\[ -z "\$\{DIVERSE_NOTES// \}" \] && DIVERSE_NOTES="\$ALL_NOTES")',
            _read("kb_dream.sh"), re.S)
        assert m, "防空转：未能抽出 DIVERSE_NOTES 赋值块"
        cls.block = m.group(1)

    def _run(self, notes, script_dir, env=None, cwd=None):
        harness = (f'SCRIPT_DIR={script_dir}\nALL_NOTES="' + "\n".join(notes) + '"\n'
                   + self.block + '\necho "$DIVERSE_NOTES"')
        return subprocess.run(["bash", "-c", harness], capture_output=True,
                              text=True, env=env, cwd=cwd)

    def test_happy_path_month_interleave(self):
        r = self._run(["/x/notes/20260401010101.md", "/x/notes/20260820010101.md",
                       "/x/notes/20260415010101.md", "/x/notes/20260601010101.md"], REPO)
        self.assertEqual(r.stdout.split()[:3],
                         ["/x/notes/20260820010101.md", "/x/notes/20260601010101.md",
                          "/x/notes/20260415010101.md"])

    def test_fail_open_preserves_original_order(self):
        env = dict(os.environ)
        env["HOME"] = "/nonexistent-home-v326"
        notes = ["/x/notes/20260401010101.md", "/x/notes/20260820010101.md"]
        r = self._run(notes, "/nonexistent", env=env, cwd="/tmp")
        self.assertEqual(r.stdout.split(), notes, "helper 不可达必须回退原序（素材不丢）")
        self.assertIn("WARN: month_round_robin", r.stderr, "降级必须 stderr 可见（MR-11）")


class TestSourceGuards(unittest.TestCase):
    def test_markers(self):
        for name in ("kb_dream.sh", "kb_dream_helpers.py"):
            self.assertIn("V37.9.326", _read(name), f"{name} 缺 V37.9.326 marker")

    def test_bash_syntax(self):
        r = subprocess.run(["bash", "-n", os.path.join(REPO, "kb_dream.sh")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
