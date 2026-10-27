#!/usr/bin/env python3
"""V37.9.336 升级 SOP 现时性守卫（第八次评估同版交付）。

血案（2026-09-01 第八次评估时发现，全部 grounded）：
`docs/gateway_upgrade_eval_v2026.4.md` 的「七、升级 SOP」是 2026-04 时点快照，
而它恰恰是「用户决定升级后照着执行」的那份清单 —— 典型 fail-plausible：
读起来完整、有命令、有 checklist，描述的却是四个月前的世界。

四处主动误导（修复前实录）：
  1. 7.0 目标版本写 v2026.4.2 —— 我们 2026-06-11 已升到 4.27，照此执行是降级。
  2. 7.5 回滚硬编码 `npm install -g openclaw@2026.3.13-1` —— 从 4.27 降到 3.13
     是跨 4 个 minor 的破坏性降级；而 7.1 明明已把真实版本写进
     ~/upgrade_before_version.txt 却没有任何步骤消费它
     （= V37.9.293「消费端建好了生产端没喂」的镜像：生产端产出了没人读）。
  3. 7.5 标题「回滚方案（30 秒）」—— 与第六次评估 17.4 的结论
     「回滚已从 30 秒无损退化为有损单向门」直接矛盾。
  4. 第六/七/八次评估累积的三项新前置（node 区间 / 插件锁步 / 默认自主行为审计）
     全部只活在评估节，SOP 清单里一项都没有。

外加原则 #35 违反：7.1–7.5 全部命令块每行带 `#` 注释，而它们正是贴给用户在
Mac Mini 上复制粘贴执行的块（zsh interactive_comments 默认 OFF 会把 `#` 当命令，
用户 2026-06-11 最终指令）。

本守卫把「SOP ↔ 评估结论」做成跨节契约（MR-8）：未来评估节新增前置而 SOP 没跟，
CI 立刻红 —— 而不是靠下一个 session 记得同步。
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

DOC = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "docs", "gateway_upgrade_eval_v2026.4.md")


def _read():
    with open(DOC, encoding="utf-8") as f:
        return f.read()


def _slice(text, start_marker, end_marker):
    i = text.index(start_marker)
    j = text.index(end_marker, i + len(start_marker))
    return text[i:j]


# 7.0 清单勾选项总数的单一真理源（V37.9.366 起）：3 基础 + A + B + C 头 + 15 子项。
# 此前 Eleventh / Twelfth / Thirteenth 三个类各自写死 20 —— 同一事实三份字面量 pin，每加一项要改三处。
# 历史：18（第十一次）→ 19（第十二次 +#149875）→ 20（第十三次 +#154068）→ 21（第十四次 +#157091）
#       → 25（第十五次 V37.9.373：+ 开窗前全量基线子项 + owner repair / exec 空输出完成事件 / 多 agent 入站兜底 三项）
#       → 26（V37.9.373 同日 26.6：+ 前置 D 两段式升级）。
# 每个评估类仍各自断言「本次新增项在清单里」，计数 pin 只负责「没有悄悄多出未登记的项」。
PREREQ_CHECKBOXES = 26


def _bash_blocks(text):
    return re.findall(r"```bash\n(.*?)```", text, re.S)


class TestNoStaleHardcodedVersion(unittest.TestCase):
    """血案 1+2：SOP 不得硬编码具体版本作为升级/回滚目标。"""

    def setUp(self):
        self.doc = _read()
        self.sop = _slice(self.doc, "## 七、升级 SOP", "## 八、综合评估")

    def test_sop_extracted_non_empty(self):
        """防空转：SOP 段必须真抽到且含各子节。"""
        self.assertGreater(len(self.sop), 1500)
        for sub in ("### 7.0", "### 7.1", "### 7.2", "### 7.3", "### 7.4", "### 7.5"):
            self.assertIn(sub, self.sop, f"SOP 缺 {sub}，切片锚点可能已漂移")

    def test_no_hardcoded_install_version_in_sop_commands(self):
        """血案回归：SOP 可执行块不得 `npm install -g openclaw@<具体版本>`。

        修复前 7.5 写死 openclaw@2026.3.13-1，从 4.27 执行即破坏性降级。
        允许占位符 <目标版本> 与从文件读回的 "openclaw@$PREV"。
        """
        for blk in _bash_blocks(self.sop):
            hits = re.findall(r"openclaw@20\d\d\.\d+[\w.\-]*", blk)
            self.assertEqual(
                [], hits,
                f"SOP 命令块硬编码了具体版本 {hits} —— 版本必须来自最新评估结论"
                f"（升级）或 upgrade_before_version.txt（回滚）")

    def test_prereq_target_version_not_hardcoded(self):
        """7.0 前置不得钉死某个目标版本（它是最新评估节的结论）。"""
        pre = _slice(self.sop, "### 7.0", "### 7.1")
        stale = re.findall(r"目标版本[：:]\s*\*{0,2}v?20\d\d\.\d+", pre)
        self.assertEqual([], stale,
                         f"7.0 硬编码目标版本 {stale}；应指向最新评估节结论")
        self.assertIn("最近一次评估", pre)

    def test_rollback_consumes_recorded_version(self):
        """生产端-消费端契约：7.1 产出的文件必须真的被 7.5 读回。

        修复前 7.1 写了 ~/upgrade_before_version.txt，而 7.5 完全不读它
        （产出了没人消费 = V37.9.293 家族的镜像形态）。
        """
        backup = _slice(self.sop, "### 7.1", "### 7.2")
        rollback = self.sop[self.sop.index("### 7.5"):]
        self.assertIn("upgrade_before_version.txt", backup,
                      "7.1 必须记录升级前版本（回滚依据）")
        self.assertIn("upgrade_before_version.txt", rollback,
                      "7.5 必须消费 7.1 记录的版本，而不是硬编码")
        self.assertTrue(
            any("upgrade_before_version.txt" in b and "npm install" in "".join(_bash_blocks(rollback))
                for b in _bash_blocks(rollback)),
            "7.5 的回滚命令块必须真的从该文件读回版本")


class TestRollbackHonesty(unittest.TestCase):
    """血案 3：回滚不得再声称「30 秒无损」。"""

    def setUp(self):
        self.doc = _read()
        self.sop = _slice(self.doc, "## 七、升级 SOP", "## 八、综合评估")
        self.rollback = self.sop[self.sop.index("### 7.5"):]

    def test_rollback_heading_drops_30_second_claim(self):
        heading = self.rollback.splitlines()[0]
        self.assertNotIn("30 秒", heading,
                         "第六次评估 17.4 已判定回滚退化为有损单向门，标题不得再承诺 30 秒")

    def test_rollback_carries_one_way_door_warning(self):
        for kw in ("有损", "SQLite"):
            self.assertIn(kw, self.rollback,
                          f"7.5 必须显式警告单向门（缺关键词「{kw}」）")

    def test_rollback_has_full_snapshot_path(self):
        """≥6.x 目标下唯一可靠回滚依据是全量快照，7.1 必须产出、7.5 必须用。"""
        backup = _slice(self.sop, "### 7.1", "### 7.2")
        self.assertIn("openclaw_full_snapshot", backup)
        self.assertIn("openclaw_full_snapshot", self.rollback)


class TestEvalToSopContract(unittest.TestCase):
    """血案 4（MR-8 跨节契约）：评估节新增的前置必须出现在 SOP 清单里。

    锚点选 PR 号而非措辞：评估节改写文案不会误报，但漏了某项前置会红。
    """

    def setUp(self):
        self.doc = _read()
        self.sop = _slice(self.doc, "## 七、升级 SOP", "## 八、综合评估")
        self.prereq = _slice(self.sop, "### 7.0", "### 7.1")

    def test_default_behavior_prs_all_present_in_prereq(self):
        """19.4 表里每个默认开关 PR 号都必须在 7.0 前置 C 出现。"""
        s194 = _slice(self.doc, "### 19.4", "### 19.5")
        prs = sorted(set(re.findall(r"#(\d{6})", s194)))
        self.assertGreaterEqual(len(prs), 6,
                                f"防空转：19.4 应含 ≥6 个默认开关 PR 号，实际 {prs}")
        missing = [p for p in prs if f"#{p}" not in self.prereq]
        self.assertEqual([], missing,
                         f"19.4 的默认自主行为 PR {missing} 未进 7.0 前置清单 —— "
                         f"评估结论与 SOP 已漂移")

    def test_node_range_from_eval_present_in_sop(self):
        """第七次评估的 node 区间必须逐字出现在 SOP（前置 A / 7.2）。"""
        s18 = _slice(self.doc, "### 18.3", "### 18.5")
        m = re.search(r">=22\.22\.3 <23 \\?\|\\?\| >=24\.15\.0 <25 \\?\|\\?\| >=25\.9\.0", s18)
        self.assertIsNotNone(m, "防空转：第十八节应含 node 区间字面量")
        norm = lambda t: t.replace("\\|", "|").replace(" ", "")
        self.assertIn(norm(m.group(0)), norm(self.sop),
                      "SOP 未携带评估节声明的 node 接受区间")

    def test_plugin_lockstep_prereq_present(self):
        """19.5 的插件锁步结论必须成为 7.0 的一项前置。"""
        for kw in ("@openclaw/whatsapp", "peerDependencies"):
            self.assertIn(kw, self.prereq,
                          f"7.0 前置 B 缺插件锁步核对要素「{kw}」")

    def test_prereq_is_a_checklist(self):
        boxes = self.prereq.count("- [ ]")
        self.assertGreaterEqual(boxes, 6,
                                f"7.0 应是可勾选清单（≥6 项），实际 {boxes}")


class TestPrinciple35NoCommentsInCommandBlocks(unittest.TestCase):
    """原则 #35：贴给用户执行的命令块内不得有注释行。

    用户 2026-06-11 最终指令：zsh interactive_comments 默认 OFF，
    `#` 会被当命令执行（已多次实际报错）。说明写块外正文。
    """

    def setUp(self):
        self.doc = _read()
        self.sop = _slice(self.doc, "## 七、升级 SOP", "## 八、综合评估")
        self.blocks = _bash_blocks(self.sop)

    def test_blocks_extracted_non_empty(self):
        self.assertGreaterEqual(len(self.blocks), 8,
                                f"防空转：SOP 应含 ≥8 个命令块，实际 {len(self.blocks)}")

    def test_no_comment_lines(self):
        offenders = []
        for i, blk in enumerate(self.blocks):
            for line in blk.splitlines():
                if line.lstrip().startswith("#"):
                    offenders.append((i, line.strip()[:60]))
        self.assertEqual([], offenders,
                         f"SOP 命令块含注释行（原则 #35）：{offenders}")


class TestDocHeadSingleSourceOfTruth(unittest.TestCase):
    """文档头「当前态」是唯一真理源，历史章节须标时点。"""

    def setUp(self):
        self.doc = _read()
        self.head = self.doc[:self.doc.index("## 一、版本概览")]

    def test_head_declares_current_state(self):
        self.assertIn("当前态", self.head)
        self.assertIn("单一真理源", self.head)
        self.assertIn("v2026.4.27", self.head,
                      "头部当前态必须写真实部署版本")

    def test_head_lists_all_evaluations(self):
        for kw in ("六次评估", "七次评估", "八次评估", "九次评估"):
            self.assertIn(kw, self.head, f"头部评估列表缺 {kw}")

    def test_overview_table_marked_point_in_time(self):
        tbl = _slice(self.doc, "## 一、版本概览", "## 二、原 Hold 条件评估")
        self.assertIn("时点快照", tbl,
                      "版本概览表是 2026-04 快照，必须显式标注")
        self.assertNotIn("| 当前部署版本 |", tbl,
                         "概览表不得再声称『当前部署版本』（陈旧 4 个月）")

    def test_head_and_prereq_point_to_latest_tracking_point(self):
        """头部当前态行与 7.0「目标版本」行必须指向最新评估节的 .5 结论小节（V37.9.366 起从 doc 标题动态派生，
        替代每个评估类各写一次字面量 pin）。"""
        token = _latest_eval(self.doc)[0]
        sec = self.doc[self.doc.index("## " + token):]
        m = re.search(r"^### (\d+)\.1 ", sec, re.M)
        self.assertIsNotNone(m, f"防空转：{token} 应含 ### NN.1 小节")
        ref = f"{token} {m.group(1)}.5"
        self.assertIn(ref, self.head, f"头部当前态须指向 {ref}")
        prereq = _slice(_slice(self.doc, "## 七、升级 SOP", "## 八、综合评估"), "### 7.0", "### 7.1")
        self.assertIn(ref, prereq, f"7.0 目标版本行须指向 {ref}")

    def test_section8_marked_point_in_time(self):
        s8 = _slice(self.doc, "## 八、综合评估", "## 九")
        self.assertIn("时点快照", s8)
        latest_token = _latest_eval(self.doc)[0]
        self.assertIn(latest_token, s8,
                      f"第八节须指向最新评估结论（{latest_token}）——V37.9.350 起从 doc 标题动态派生，评估节新增不必再改本 pin")


class TestCriterion1CheckProtocol(unittest.TestCase):
    """19.8 判据 ① 核对协议守卫（同日追加）。

    血案：对 2026.9.1-beta.1 做提前读数时，`sqlite`/`migrat` 计数全为 0——
    读起来像「上游终于收敛了」，实际是 changelog 内容还没写
    （1,520 条无描述的裸 PR 行 + 仅 17 条叙述；对比 8.1 = 506、7.1 = 1719）。
    这个假绿的误判方向是最贵的那个：会让判据 ① 连续计数错误 +1 → 提前开升级窗口。
    同族 V37.9.288「搜索坏了 ≠ 没搜到」/ V37.9.310 / V37.9.322。

    设计协议时还有第二次自我证伪：最初想「只扫叙述段」，被 7.1 否决——
    它的 session-accessor 证据（#101178/#101179）住在 Complete contribution record
    段且带描述（叙述段只有 sqlite=2/migrat=3，全文 12/15）。故协议固定两条：
    整份扫描 + 内容门槛按「叙述条目 + 带描述 PR 条目」计。

    本守卫钉住这两条，防止未来有人把协议简化回裸 grep。
    """

    def setUp(self):
        self.doc = _read()
        self.sec = _slice(self.doc, "### 19.8", "\n---\n")
        blocks = _bash_blocks(self.sec)
        self.assertEqual(1, len(blocks), "19.8 应恰有 1 个协议命令块")
        self.block = blocks[0]

    def test_section_exists_and_records_na_not_clean(self):
        """beta.1 读数必须记为『不可判』，绝不能被记成『干净』。"""
        self.assertIn("N/A_NO_CONTENT", self.sec)
        self.assertIn("不可判", self.sec)
        self.assertNotIn("beta.1 干净", self.sec)

    def test_protocol_python_compiles(self):
        """协议里的内嵌 python 必须真能编译（防转义在入文档时被破坏）。"""
        m = re.search(r"<<'PYCHK'\n(.*?)\nPYCHK", self.block, re.S)
        self.assertIsNotNone(m, "协议块未找到 PYCHK heredoc")
        compile(m.group(1), "<protocol>", "exec")

    def test_anti_vacuous_gate_present(self):
        """防空转门槛：内容不足必须判不可判并以退出码 3 区分。"""
        self.assertIn("semantic < 100", self.block,
                      "协议缺内容门槛——0 计数会被误读为干净")
        self.assertIn("N/A_NO_CONTENT", self.block)
        self.assertIn("sys.exit(3)", self.block,
                      "不可判必须有独立退出码，不能与 CLEAN(0)/DIRTY(2) 混同")

    def test_scan_is_whole_file_not_narrative_only(self):
        """扫描范围必须是整份 changelog（7.1 的证据在 contribution record 段）。"""
        line = [l for l in self.block.splitlines() if l.startswith("hits = re.findall(")]
        self.assertEqual(1, len(line), "协议未找到迁移关键词扫描行")
        m = re.search(r",\s*(\w+)\)\s*$", line[0])
        self.assertIsNotNone(m, f"扫描行末尾解析失败: {line[0][-60:]}")
        self.assertEqual("cl", m.group(1),
                         "迁移关键词必须扫整份 changelog(cl)，只扫叙述段会漏掉 "
                         "7.1 的 session-accessor 证据")

    def test_described_pr_excludes_bare_entries(self):
        """裸 PR 行与仅 Thanks/Related 的条目不得计入内容量。"""
        for kw in ("Thanks", "Related", "Fixes", "Closes"):
            self.assertIn(kw, self.block,
                          f"内容量统计须排除仅 {kw} 的无语义条目")
        self.assertIn("bare_pr", self.block, "协议须显式报出裸 PR 行数供人核对")

    def test_threshold_backed_by_three_measurements(self):
        """门槛 100 必须有实测数据支撑（原则 #36-4：数字要对账）。"""
        for n in ("506", "1719", "17"):
            self.assertIn(n, self.sec,
                          f"19.8 缺实测数据点 {n}（7.1/8.1/beta.1 三点）")
        self.assertIn("诚实边界", self.sec, "门槛只有 3 个数据点，须登记不确定性")

    def test_npm_noise_suppressed(self):
        """协议是贴给人跑的：npm notice 走 stderr，须一并抑制。"""
        self.assertIn("npm pack", self.block)
        self.assertRegex(self.block, r"npm pack [^\n]*>/dev/null 2>&1",
                         "npm pack 需 2>&1，否则 11k 行 notice 淹没结论")

    def test_protocol_block_has_no_comment_lines(self):
        """原则 #35 同样适用于 19.8 的可执行块。"""
        offenders = [l.strip()[:60] for l in self.block.splitlines()
                     if l.lstrip().startswith("#")]
        self.assertEqual([], offenders, f"19.8 命令块含注释行：{offenders}")

    def test_tracking_point_routes_through_protocol(self):
        """19.7 的下次跟踪点必须指向本协议，而不是裸 grep。"""
        s197 = _slice(self.doc, "### 19.7", "### 19.8")
        self.assertIn("19.8", s197, "跟踪点未指向核对协议")


class TestEighthEvaluationPresent(unittest.TestCase):
    """第八次评估自身的落地守卫。"""

    def setUp(self):
        self.doc = _read()

    def test_section19_exists_with_all_subsections(self):
        for sub in ("## 第十九节", "### 19.1", "### 19.2", "### 19.3",
                    "### 19.4", "### 19.5", "### 19.6", "### 19.7"):
            self.assertIn(sub, self.doc, f"第十九节缺 {sub}")

    def test_last_eval_date_synced(self):
        """check_upgrade.sh 的 LAST_EVAL_DATE 必须与最新评估节的日期一致（V37.9.350 起动态派生）。"""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "check_upgrade.sh")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        m = re.search(r'LAST_EVAL_DATE="\$\{OPENCLAW_LAST_EVAL_DATE:-([\d-]+)\}"', src)
        self.assertIsNotNone(m, "check_upgrade.sh 未找到 LAST_EVAL_DATE 默认值")
        token, ordinal, day = _latest_eval(_read())
        self.assertEqual(day, m.group(1),
                         f"第{ordinal}次评估完成后 LAST_EVAL_DATE 必须重置为 {day}")
        self.assertIn(token, src, f"注释须指向最新评估节（{token}）")


_EVAL_HEADING = re.compile(
    r"^## (第[一二三四五六七八九十]+节)：第([一二三四五六七八九十]+)次评估（(\d{4}-\d{2}-\d{2})", re.M)


def _latest_eval(doc):
    """从 doc 标题派生最新评估节（token, 序数, 日期）——V37.9.350 起不再逐次改字面量 pin。"""
    ms = list(_EVAL_HEADING.finditer(doc))
    assert len(ms) >= 4, f"防空转：应至少抽到第六~九次评估标题，实际 {len(ms)}"
    m = ms[-1]
    return m.group(1), m.group(2), m.group(3)


class TestNinthEvaluationPresent(unittest.TestCase):
    """第九次评估（V37.9.350，2026-09-06）自身的落地守卫。

    三个新 stable（8.2/9.1/9.2）触发判据跟踪 → 第二十节；上游 changelog 换格式暴露
    19.8 协议的计量单位失效 → 协议就地重标定；9.2 再加 3 项默认自主行为 → 7.0 前置 C 追加。
    """

    def setUp(self):
        self.doc = _read()
        self.sec20 = _slice(self.doc, "## 第二十节", "\n---\n")
        self.prereq = _slice(_slice(self.doc, "## 七、升级 SOP", "## 八、综合评估"),
                             "### 7.0", "### 7.1")

    def test_section20_exists_with_all_subsections(self):
        for sub in ("### 20.1", "### 20.2", "### 20.3", "### 20.4", "### 20.5", "### 20.6"):
            self.assertIn(sub, self.sec20, f"第二十节缺 {sub}")

    def test_ninth_eval_heading_present(self):
        """第九次评估标题存在且日期正确——用成员断言而非「== 最新」，第十次评估只需新增类不改本 pin。"""
        found = [(m.group(1), m.group(2), m.group(3)) for m in _EVAL_HEADING.finditer(self.doc)]
        self.assertIn(("第二十节", "九", "2026-09-06"), found, found)

    def test_new_default_behavior_prs_in_prereq_c(self):
        """20.5 表里的 3 项新默认变更 PR 号都必须进 7.0 前置 C（跨节契约，镜像 19.4 守卫）。"""
        s205 = _slice(self.sec20, "### 20.5", "### 20.6")
        tbl = _slice(s205, "| 版本 · 默认变更", "**持有成本复测")
        prs = sorted(set(re.findall(r"#(\d{6})", tbl)))
        self.assertGreaterEqual(len(prs), 3, f"防空转：20.5 表应含 ≥3 个 PR 号，实际 {prs}")
        missing = [p for p in prs if f"#{p}" not in self.prereq]
        self.assertEqual([], missing, f"20.5 的默认行为 PR {missing} 未进 7.0 前置 C")
        self.assertGreaterEqual(self.prereq.count("- [ ]"), 16,
                                "前置 C 追加 3 项后 7.0 清单应 ≥16 个勾选项（3 基础 + A + B + C 头 + 10 子项）")

    def test_criterion1_verdicts_recorded_dirty_not_clean(self):
        """三个新 stable 的判定必须记为 DIRTY；计数 0；绝不能出现「9.1 干净」类误记。"""
        s203 = _slice(self.sec20, "### 20.3", "### 20.4")
        for v in ("8.2", "9.1", "9.2"):
            self.assertIn(v, s203)
        self.assertIn("DIRTY", s203)
        self.assertIn("计数 0", s203)
        for bad in ("9.1 干净", "9.2 干净", "8.2 干净", "计数 1"):
            self.assertNotIn(bad, self.sec20, f"不得出现误记「{bad}」")

    def test_protocol_recalibration_documented_with_six_points(self):
        """20.4 必须给出六点实测表（原则 #36-4：数字要对账）。"""
        s204 = _slice(self.sec20, "### 20.4", "### 20.5")
        for n in ("924", "143", "802", "419", "291", "| 10 |", "1719", "506"):
            self.assertIn(n, s204.replace("**10**", "| 10 |") if n == "| 10 |" else s204,
                          f"20.4 缺实测数据点 {n}")
        self.assertIn("inline_pr", s204)

    def test_tracking_point_routes_through_amended_protocol(self):
        s206 = _slice(self.sec20, "### 20.6", "**LAST_EVAL_DATE")
        self.assertIn("19.8", s206, "下次跟踪点须指向 19.8 协议")
        self.assertIn("修订", s206)


class TestCriterion1ProtocolAmendment(unittest.TestCase):
    """19.8 协议重标定守卫：行内 PR 引用量是 load-bearing 的第二把尺子。"""

    def setUp(self):
        self.doc = _read()
        self.sec = _slice(self.doc, "### 19.8", "\n---\n")
        self.block = _bash_blocks(self.sec)[0]

    def test_inline_pr_gate_present(self):
        self.assertIn("inline_pr", self.block, "协议缺行内 PR 引用量（新格式下会永久不可判）")
        self.assertRegex(self.block, r"if semantic < 100 and inline_pr < 50:",
                         "不可判条件必须是两把尺子同时低")
        self.assertIn("inline_pr_refs=", self.block, "协议须显式报出行内引用数供人核对")

    def test_amendment_recorded_with_data(self):
        self.assertIn("修订（2026-09-06", self.sec)
        for n in ("924", "143", "77", "69"):
            self.assertIn(n, self.sec, f"19.8 修订段缺实测数据点 {n}")
        self.assertIn("计量单位错了", self.sec)


class TestCriterion1ProtocolBehavior(unittest.TestCase):
    """从 19.8 抽出协议 python 真跑（V37.9.336 手动做过一次，本版机器化）。

    骨架（beta.1 形态）→ rc 3；新格式带迁移 → rc 2；新格式干净 → rc 0；
    旧格式 described-PR 带迁移 → rc 2（整份扫描）；骨架即使含关键词也 rc 3（不可判优先，防假计数）。
    """

    @classmethod
    def setUpClass(cls):
        doc = _read()
        block = _bash_blocks(_slice(doc, "### 19.8", "\n---\n"))[0]
        m = re.search(r"<<'PYCHK'\n(.*?)\nPYCHK", block, re.S)
        assert m, "协议块未找到 PYCHK heredoc"
        cls.code = m.group(1)

    def _run(self, changelog):
        import subprocess
        import sys
        import tarfile
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            md = os.path.join(tmp, "CHANGELOG.md")
            with open(md, "w", encoding="utf-8") as f:
                f.write(changelog)
            with tarfile.open(os.path.join(tmp, "openclaw-0.0.0.tgz"), "w:gz") as tf:
                tf.add(md, arcname="package/CHANGELOG.md")
            p = subprocess.run([sys.executable, "-c", self.code], cwd=tmp,
                               capture_output=True, text=True, timeout=60)
            return p.returncode, p.stdout

    @staticmethod
    def _skeleton(with_keyword=False):
        bullets = [f"- **Topic {i}:** minor wording polish number {i}." for i in range(17)]
        if with_keyword:
            bullets[0] = "- **Topic 0:** SQLite wording polish."
        bare = [f"- **PR #{140000 + i}**" for i in range(1520)]
        return "# Changelog\n\n## 0.0.0\n\n### Highlights\n\n" + "\n".join(bullets) + \
            "\n\n### Complete contribution record\n\n#### Pull requests\n\n" + "\n".join(bare) + "\n"

    @staticmethod
    def _new_format(dirty):
        bullets = []
        for i in range(70):
            a, b = 150000 + 2 * i, 150001 + 2 * i
            bullets.append(f"- **Topic {i}:** consolidated paragraph covering two changes. (#{a}, #{b})")
        if dirty:
            bullets[3] = "- **Upgrade data safety:** identical session event replays migrate cleanly. (#150999)"
        bare = [f"- **PR #{160000 + i}**" for i in range(1000)]
        return "# Changelog\n\n## 0.0.0\n\n### Changes\n\n" + "\n".join(bullets) + \
            "\n\n### Complete contribution record\n\n#### Pull requests\n\n" + "\n".join(bare) + "\n"

    @staticmethod
    def _old_format(dirty):
        lines = [f"- **PR #{170000 + i}** [fix(ui): polish item {i}](https://x/{i})" for i in range(120)]
        if dirty:
            lines[7] = "- **PR #170007** [perf(state): streamline SQLite schema comparison](https://x/7)"
        return "# Changelog\n\n## 0.0.0\n\n### Fixes\n\n- **Only:** one narrative line.\n\n" + \
            "### Complete contribution record\n\n#### Pull requests\n\n" + "\n".join(lines) + "\n"

    def test_skeleton_is_not_decidable(self):
        rc, out = self._run(self._skeleton())
        self.assertEqual(3, rc, out)
        self.assertIn("N/A_NO_CONTENT", out)

    def test_skeleton_with_keyword_still_not_decidable(self):
        """不可判优先于命中：骨架里的关键词不得让计数归零或 +1（两侧都不计）。"""
        rc, out = self._run(self._skeleton(with_keyword=True))
        self.assertEqual(3, rc, out)

    def test_new_format_dirty_is_decidable_dirty(self):
        rc, out = self._run(self._new_format(dirty=True))
        self.assertEqual(2, rc, out)
        self.assertIn("VERDICT=DIRTY", out)

    def test_new_format_clean_is_decidable_clean(self):
        """行内引用量 ≥50 让新格式可判——这是修订前做不到的（旧尺子给 70 < 100）。"""
        rc, out = self._run(self._new_format(dirty=False))
        self.assertEqual(0, rc, out)
        self.assertIn("VERDICT=CLEAN", out)
        self.assertRegex(out, r"semantic_entries=70 ")

    def test_old_format_scans_whole_file(self):
        rc, out = self._run(self._old_format(dirty=True))
        self.assertEqual(2, rc, out)
        rc0, out0 = self._run(self._old_format(dirty=False))
        self.assertEqual(0, rc0, out0)



class TestTenthEvaluationPresent(unittest.TestCase):
    """第十次评估（V37.9.351，2026-09-09）自身的落地守卫。

    2026.9.3（09-08）= 第九次评估 20.6 预设的「下一个 stable」到期 → 第二十一节；
    判据 ② 的「积压冲刷」假设被 3 天间隔证伪；engines 首次在 8.x/9.x 线内收紧；
    9.3 再加 2 项默认自主行为 → 7.0 前置 C 追加（10 → 12）。
    """

    def setUp(self):
        self.doc = _read()
        self.sec21 = _slice(self.doc, "## 第二十一节", "\n---\n")
        self.sop = _slice(self.doc, "## 七、升级 SOP", "## 八、综合评估")
        self.prereq = _slice(self.sop, "### 7.0", "### 7.1")

    def test_section21_exists_with_all_subsections(self):
        for sub in ("### 21.1", "### 21.2", "### 21.3", "### 21.4", "### 21.5"):
            self.assertIn(sub, self.sec21, f"第二十一节缺 {sub}")

    def test_tenth_eval_heading_present(self):
        found = [(m.group(1), m.group(2), m.group(3)) for m in _EVAL_HEADING.finditer(self.doc)]
        self.assertIn(("第二十一节", "十", "2026-09-09"), found, found)

    def test_new_default_behavior_prs_in_prereq_c(self):
        """21.4 表里的 2 项新默认变更 PR 号都必须进 7.0 前置 C（跨节契约，镜像 19.4/20.5 守卫）。"""
        s214 = _slice(self.sec21, "### 21.4", "### 21.5")
        tbl = _slice(s214, "| 版本 · 默认变更", "**持有成本复测")
        rows = "\n".join(l for l in tbl.splitlines() if l.startswith("|"))
        prs = sorted(set(re.findall(r"#(\d{6})", rows)))
        self.assertGreaterEqual(len(prs), 2, f"防空转：21.4 表应含 ≥2 个 PR 号，实际 {prs}")
        excluded = sorted(set(re.findall(r"#(\d{6})", tbl)) - set(prs))
        self.assertTrue(excluded, "防空转：21.4 表后应有「登记但不入前置 C」的散文 PR，证明只取表行的切片真在起作用")
        for p in excluded:
            self.assertNotIn(f"#{p}", self.prereq, f"非自主 LLM 行为 #{p} 不该进前置 C（21.4 明写登记但不入）")
        missing = [p for p in prs if f"#{p}" not in self.prereq]
        self.assertEqual([], missing, f"21.4 的默认行为 PR {missing} 未进 7.0 前置 C")
        self.assertGreaterEqual(self.prereq.count("- [ ]"), 18,
                                "前置 C 追加 2 项后 7.0 清单应 ≥18 个勾选项（3 基础 + A + B + C 头 + 12 子项）")

    def test_criterion1_verdict_dirty_and_interval_recorded(self):
        """9.3 必须记 DIRTY 且计数 0；② 的 3 天间隔必须落在「持续未满足」而非「回 ✅」；不得误记「9.3 干净」。"""
        s213 = _slice(self.sec21, "### 21.3", "### 21.4")
        self.assertIn("9.3", s213)
        self.assertIn("DIRTY", s213)
        self.assertIn("计数仍 0", s213)
        self.assertIn("3 天", s213)
        self.assertIn("持续未满足", s213)
        for bad in ("9.3 干净", "计数 1", "② 回 ✅", "冲刷」假设成立"):
            self.assertNotIn(bad, self.sec21, f"不得出现误记「{bad}」")

    def test_node_range_9_3_present_in_sop_prereq_a(self):
        """9.3 收紧后的 engines 区间必须逐字进 SOP 前置 A（镜像第七次评估 node 区间跨节契约）。"""
        s213 = _slice(self.sec21, "### 21.3", "### 21.4")
        m = re.search(r">=24\.16\.0 <25 \\?\|\\?\| >=26\.1\.0", s213)
        self.assertIsNotNone(m, "防空转：21.3 应含 9.3 的 node 区间字面量")
        norm = lambda t: t.replace("\\|", "|").replace(" ", "")
        self.assertIn(norm(m.group(0)), norm(self.prereq),
                      "7.0 前置 A 未携带 9.3 收紧后的 node 接受区间")

    def test_protocol_seventh_point_recorded(self):
        """21.4 必须给出 9.3 的协议实测读数（叙述 203 / 行内 859），原则 #36-4 数字对账。"""
        s214 = _slice(self.sec21, "### 21.4", "### 21.5")
        for n in ("203", "859", "1843"):
            self.assertIn(n, s214, f"21.4 缺 9.3 协议实测数据点 {n}")

    def test_tracking_point_routes_through_amended_protocol(self):
        s215 = _slice(self.sec21, "### 21.5", "**LAST_EVAL_DATE")
        self.assertIn("19.8", s215, "下次跟踪点须指向 19.8 协议")
        self.assertIn("修订", s215)
        self.assertIn("engines.node", s215, "③ 须进入每次 stable 的常规复核")


class TestEleventhEvaluationPresent(unittest.TestCase):
    """第十一次评估（V37.9.353，2026-09-13）自身的落地守卫。

    2026.9.4（09-11）= 第十次评估 21.5 预设的「下一个 stable」到期 → 第二十二节；
    判据 ② 连续两个 3 天间隔 → 本节预注册评估节奏的日落规则（三项全同只追加读数不新开节）；
    第四类风险经 dist 源码核实零新增（两项候选均为门控/opt-in，登记不入前置 C）；
    前置 C 第 12 项被 9.4 部分缓解 → 原位加注不撤项。
    """

    def setUp(self):
        self.doc = _read()
        self.head = self.doc[:self.doc.index("## 一、版本概览")]
        self.sec22 = _slice(self.doc, "## 第二十二节", "\n---\n")
        self.sop = _slice(self.doc, "## 七、升级 SOP", "## 八、综合评估")
        self.prereq = _slice(self.sop, "### 7.0", "### 7.1")

    def test_section22_exists_with_all_subsections(self):
        for sub in ("### 22.1", "### 22.2", "### 22.3", "### 22.4", "### 22.5"):
            self.assertIn(sub, self.sec22, f"第二十二节缺 {sub}")

    def test_eleventh_eval_heading_present_and_listed_in_head(self):
        found = [(m.group(1), m.group(2), m.group(3)) for m in _EVAL_HEADING.finditer(self.doc)]
        self.assertIn(("第二十二节", "十一", "2026-09-13"), found, found)
        for kw in ("十次评估", "十一次评估"):
            self.assertIn(kw, self.head, f"头部评估列表缺 {kw}")
        self.assertIn("2026.9.4", self.head, "头部当前态行须写上游 latest 2026.9.4")

    def test_candidates_registered_but_not_added_to_prereq_c(self):
        """22.4 明写零新增：候选表里的 PR 号一个都不得进 7.0 前置 C，且清单勾选项数不变（18）。"""
        s224 = _slice(self.sec22, "### 22.4", "### 22.5")
        self.assertIn("零新增", s224)
        tbl = _slice(s224, "| 9.4 候选", "**前置 C 第 12 项")
        rows = "\n".join(l for l in tbl.splitlines() if l.startswith("|"))
        prs = sorted(set(re.findall(r"#(\d{6})", rows)))
        self.assertGreaterEqual(len(prs), 2, f"防空转：22.4 候选表应含 ≥2 个 PR 号，实际 {prs}")
        for p in prs:
            self.assertNotIn(f"#{p}", self.prereq, f"9.4 候选 #{p} 已核实为门控/opt-in，不得进前置 C（22.4 明写登记不入）")
        # V37.9.354 演进（V37.9.131 alternation）：9.5 真新增第 13 项（#149875），18 → 19；
        # 本断言的意图不变——9.4 的两项候选没有以「悄悄多一项」的形式混进清单。
        # V37.9.360 演进（V37.9.131 alternation）：9.6 预防性新增第 14 项（Tool Search），19 → 20；意图不变——9.4 候选没有混进清单。
        self.assertEqual(self.prereq.count("- [ ]"), PREREQ_CHECKBOXES,
                         "7.0 清单勾选项数须等于 PREREQ_CHECKBOXES；若真需新增请同步演进该常量")

    def test_prereq_c_item12_carries_9_4_mitigation_without_removal(self):
        """#139495（9.3 更新自动推理修复）仍在前置 C，且同一行带 9.4 的缓解注记（#143767 家族）。"""
        line = next((l for l in self.prereq.splitlines() if "#139495" in l), None)
        self.assertIsNotNone(line, "前置 C 第 12 项 #139495 不得因 9.4 部分缓解而撤项")
        self.assertIn("#143767", line, "第 12 项须原位注记 9.4 的缓解（auto triage 启动 coding agent 前需确认）")
        s224 = _slice(self.sec22, "### 22.4", "### 22.5")
        self.assertIn("#143767", s224)
        self.assertIn("不撤项", s224)

    def test_criterion1_verdict_dirty_and_interval_recorded(self):
        """9.4 必须记 DIRTY 且计数 0；② 连续两个 3 天间隔必须落「持续未满足」；不得误记「9.4 干净」。"""
        s223 = _slice(self.sec22, "### 22.3", "### 22.4")
        self.assertIn("9.4", s223)
        self.assertIn("DIRTY", s223)
        self.assertIn("计数仍 0", s223)
        self.assertIn("3 天", s223)
        self.assertIn("持续未满足", s223)
        self.assertIn("单向门", s223, "上游在 9.4 changelog 自述 schema 变更阻断自动回滚 = 17.4 单向门被追认，须记录")
        for bad in ("9.4 干净", "计数 1", "② 回 ✅", "假设成立"):
            self.assertNotIn(bad, self.sec22, f"不得出现误记「{bad}」")

    def test_node_range_unchanged_and_prereq_a_has_service_runtime_check(self):
        """9.4 engines 与 9.3 逐字同须记录；前置 A 须新增「升级后核实服务 Node 未被 updater 替换」。"""
        s223 = _slice(self.sec22, "### 22.3", "### 22.4")
        m = re.search(r">=24\.16\.0 <25 \\?\|\\?\| >=26\.1\.0", s223)
        self.assertIsNotNone(m, "防空转：22.3 应含 9.4 的 node 区间字面量")
        self.assertIn("逐字相同", s223)
        pre_a = _slice(self.prereq, "前置 A", "前置 B")
        self.assertIn("9.4", pre_a, "前置 A 须登记 9.4 区间未变")
        # 钉可行动的核对短语本身——首版只钉 "updater"/"替换" 两个词，被前置 A 里描述 9.4 行为的
        # 陈述句同时满足，删掉核对句照样绿（sabotage S7 首轮没开火，V37.9.325「守卫被描述句满足」家族）。
        self.assertIn("升级后核实", pre_a, "前置 A 须含可行动核对：升级后核实服务 Node 运行时")
        self.assertIn("未被 updater 替换", pre_a, "前置 A 的核对句须明写「未被 updater 替换」")

    def test_prereq_b_carries_sdk_deprecation_notes(self):
        pre_b = _slice(self.prereq, "前置 B", "前置 C")
        self.assertIn("#143238", pre_b, "前置 B 须登记 credential-prompt 字符串签名弃用（2026-11-30 到期）")
        self.assertIn("removal-pending", pre_b, "前置 B 须记录 context aliases 到期未移除的现状")

    def test_protocol_eighth_point_recorded_in_both_sections(self):
        """22.4 给出 9.4 协议实测读数（叙述 59 / 行内 331 / 裸 1174），且 20.4 表同步追加第八行（跨节对账）。"""
        s224 = _slice(self.sec22, "### 22.4", "### 22.5")
        for n in ("59", "331", "1174"):
            self.assertIn(n, s224, f"22.4 缺 9.4 协议实测数据点 {n}")
        s204 = _slice(self.doc, "### 20.4", "### 20.5")
        row = next((l for l in s204.splitlines() if l.startswith("|") and "2026.9.4" in l), None)
        self.assertIsNotNone(row, "20.4 表须追加 2026.9.4 一行（六点表 → 八点）")
        self.assertIn("331", row)
        self.assertIn("DIRTY", row)

    def test_tracking_point_routes_through_protocol_and_sunset_rule(self):
        s225 = _slice(self.sec22, "### 22.5", "**LAST_EVAL_DATE")
        self.assertIn("19.8", s225, "下次跟踪点须指向 19.8 协议")
        self.assertIn("修订", s225)
        self.assertIn("engines.node", s225, "③ 须进入每次 stable 的常规复核")
        self.assertIn("不新开评估节", s225, "须预注册评估节奏日落规则：三项全同只追加读数")
        self.assertIn("不重置 LAST_EVAL_DATE", s225, "追加读数不重置正式评估日期（否则 _latest_eval 守卫与 tripwire 语义漂移）")
        self.assertIn("裸 grep", s225, "追加读数仍须走协议，不得简化为裸 grep")

class TestTwelfthEvaluationPresent(unittest.TestCase):
    """第十二次评估（V37.9.354，2026-09-22）自身的落地守卫。

    2026.9.5（09-19）= 第十一次评估 22.5 预设的「下一个 stable」到期；判据 ② 由 🔴 变 🟡
    （9.4→9.5 间隔 7.94 天）= 日落规则「任一判据状态变化」→ 新开第二十三节而非追加读数；
    第四类风险 +1（跨 provider 消息默认开 #149875 入前置 C 第 13 项）；并发上限 `cpus × 4`
    原位加注第 7 项（#147423，同一旋钮不新增项）；Daybreak cyberFailover 经 dist 核实登记不入；
    预注册 tripwire [2/6] 版本差距触发日的处置。
    """

    def setUp(self):
        self.doc = _read()
        self.head = self.doc[:self.doc.index("## 一、版本概览")]
        self.sec23 = _slice(self.doc, "## 第二十三节", "\n---\n")
        self.sop = _slice(self.doc, "## 七、升级 SOP", "## 八、综合评估")
        self.prereq = _slice(self.sop, "### 7.0", "### 7.1")

    def test_section23_exists_with_all_subsections(self):
        for sub in ("### 23.1", "### 23.2", "### 23.3", "### 23.4", "### 23.5"):
            self.assertIn(sub, self.sec23, f"第二十三节缺 {sub}")

    def test_twelfth_eval_heading_present_and_listed_in_head(self):
        found = [(m.group(1), m.group(2), m.group(3)) for m in _EVAL_HEADING.finditer(self.doc)]
        self.assertIn(("第二十三节", "十二", "2026-09-22"), found, found)
        for kw in ("十一次评估", "十二次评估"):
            self.assertIn(kw, self.head, f"头部评估列表缺 {kw}")
        self.assertIn("2026.9.5", self.head, "头部当前态行须写上游 latest 2026.9.5")

    def test_new_section_justified_by_criterion_change_not_appended(self):
        """22.5 日落规则：三项全同才追加读数；本次 ② 变 🟡 = 状态变化，必须以新开节的形式落地并写明理由。"""
        self.assertIn("日落规则", self.sec23)
        self.assertIn("新开", self.sec23, "须写明为何新开节而非追加读数")
        self.assertNotIn("### 22.6", self.doc, "判据状态变化时不得走「22.6 追加读数」路径")

    def test_prereq_c_gains_cross_provider_item_13(self):
        """#149875 默认翻转（dist 实证 9.4 `=== true` → 9.5 `!== false`）必须成为前置 C 第 13 项，且清单恰 19 项。"""
        self.assertIn("#149875", self.prereq, "9.5 跨 provider 消息默认开未进 7.0 前置 C")
        line = next((l for l in self.prereq.splitlines() if "#149875" in l), None)
        self.assertIsNotNone(line)
        self.assertIn("allowAcrossProviders", line, "第 13 项须写出可行动的配置键")
        self.assertIn("首次启动前", line, "第 13 项须写明在首次启动前显式关闭")
        # V37.9.360 演进：第 14 项（Tool Search 预防性）加入后 19 → 20，第 13 项本身不变。
        self.assertEqual(self.prereq.count("- [ ]"), PREREQ_CHECKBOXES)
        s234 = _slice(self.sec23, "### 23.4", "### 23.5")
        self.assertIn("第 13 项", s234)

    def test_candidate_table_verdicts_match_prereq_c(self):
        """23.4 候选表：标「登记不入」的 PR 一个都不得进前置 C；标「入前置 C」/「原位加注」的 PR 必须都在。"""
        s234 = _slice(self.sec23, "### 23.4", "### 23.5")
        tbl = _slice(s234, "| 9.5 候选", "另三项按同一尺子核过不入")
        rows = [l for l in tbl.splitlines() if l.startswith("| **")]
        self.assertGreaterEqual(len(rows), 3, f"防空转：候选表应含 ≥3 行，实际 {len(rows)}")
        seen_exclude = seen_include = 0
        for row in rows:
            prs = sorted(set(re.findall(r"#(\d{6})", row)))
            self.assertTrue(prs, f"防空转：候选行无 PR 号：{row[:60]}")
            if "登记不入" in row:
                seen_exclude += 1
                for p in prs:
                    self.assertNotIn(f"#{p}", self.prereq, f"候选 #{p} 已核实登记不入，不得进前置 C")
            elif "入前置 C" in row or "原位加注" in row:
                seen_include += 1
                for p in prs:
                    self.assertIn(f"#{p}", self.prereq, f"候选 #{p} 结论为入/加注，前置 C 却没有")
            else:
                self.fail(f"候选行缺明确结论（登记不入 / 入前置 C / 原位加注）：{row[:80]}")
        self.assertGreaterEqual(seen_exclude, 1, "防空转：应至少有一项登记不入（Daybreak）")
        self.assertGreaterEqual(seen_include, 2, "防空转：应至少有两项进清单（#149875 新增 + #147423 原位加注）")

    def test_prereq_c_item7_carries_9_5_amplification_in_place(self):
        """#114047（并发假设）同一行带 9.5 的 `cpus × 4` 注记（#147423），且没有被拆成第二个并发项。"""
        line = next((l for l in self.prereq.splitlines() if "#114047" in l), None)
        self.assertIsNotNone(line, "前置 C 第 7 项 #114047 不得被撤")
        self.assertIn("#147423", line, "第 7 项须原位注记 9.5 放大（同一旋钮 defaultAgentMaxConcurrent）")
        self.assertIn("cpus × 4", line)
        self.assertEqual(sum(1 for l in self.prereq.splitlines() if "#147423" in l), 1,
                         "#147423 只应出现在第 7 项那一行（原位加注不新增项）")

    def test_criterion1_dirty_and_interval_yellow_recorded_honestly(self):
        """9.5 必须记 DIRTY 且计数 0；② 必须记 🟡 并带 7.94 天与滚动速率；不得误记「9.5 干净」或把 🟡 写成 ✅。"""
        s233 = _slice(self.sec23, "### 23.3", "### 23.4")
        self.assertIn("9.5", s233)
        self.assertIn("DIRTY", s233)
        self.assertIn("计数仍 0", s233)
        self.assertIn("7.94", s233, "② 须记精确间隔 7.94 天")
        self.assertIn("🟡", s233)
        self.assertIn("1.75/周", s233, "② 须同时记 4 周窗滚动速率（单点间隔与滚动速率相反读数）")
        for bad in ("9.5 干净", "计数 1", "② ✅", "② 回 ✅", "节奏已收敛"):
            self.assertNotIn(bad, self.sec23, f"不得出现误记「{bad}」")

    def test_node_range_unchanged_and_prereq_a_mentions_9_5(self):
        s233 = _slice(self.sec23, "### 23.3", "### 23.4")
        m = re.search(r">=24\.16\.0 <25 \\?\|\\?\| >=26\.1\.0", s233)
        self.assertIsNotNone(m, "防空转：23.3 应含 9.5 的 node 区间字面量")
        self.assertIn("逐字相同", s233)
        pre_a = _slice(self.prereq, "前置 A", "前置 B")
        self.assertIn("9.5", pre_a, "前置 A 须登记 9.5 区间未变")

    def test_prereq_b_carries_weixin_stable_and_gateway_v2_notes(self):
        pre_b = _slice(self.prereq, "前置 B", "前置 C")
        self.assertIn("2.4.9", pre_b, "前置 B 须登记 weixin 2.4.9 已转 stable 而 peer floor 未变")
        self.assertIn("Gateway V2", pre_b, "前置 B 须登记 9.5 Plugin SDK Gateway V2 传输迁移")
        self.assertIn("node.client.webSocket", pre_b)

    def test_protocol_ninth_point_recorded_in_both_sections(self):
        """23.4 给出 9.5 协议实测读数（叙述 54 / 行内 217 / 裸 4245），且 20.4 表同步追加第九行（跨节对账）。"""
        s234 = _slice(self.sec23, "### 23.4", "### 23.5")
        for n in ("54", "217", "4245"):
            self.assertIn(n, s234, f"23.4 缺 9.5 协议实测数据点 {n}")
        s204 = _slice(self.doc, "### 20.4", "### 20.5")
        row = next((l for l in s204.splitlines() if l.startswith("|") and "2026.9.5" in l), None)
        self.assertIsNotNone(row, "20.4 表须追加 2026.9.5 一行（八点表 → 九点）")
        self.assertIn("217", row)
        self.assertIn("DIRTY", row)

    def test_tracking_point_preregisters_version_gap_tripwire(self):
        """23.5 须指向 19.8 协议 + 日落规则三要素，并预注册 tripwire [2/6] 触发日的处置（不预先改机器）。"""
        s235 = _slice(self.sec23, "### 23.5", "**LAST_EVAL_DATE")
        self.assertIn("19.8", s235)
        self.assertIn("engines.node", s235)
        self.assertIn("不新开评估节", s235)
        self.assertIn("不重置 LAST_EVAL_DATE", s235)
        self.assertIn("裸 grep", s235)
        self.assertIn("[2/6]", s235, "须预注册版本差距 tripwire 触发日处置")
        self.assertIn("不预先改机器", s235, "预注册须明确本节不改 tripwire 机器（日落法）")
        for opt in ("改口径", "调阈值", "接受每周告警"):
            self.assertIn(opt, s235, f"触发日三选一须写明选项「{opt}」及代价")


class TestThirteenthEvaluationPresent(unittest.TestCase):
    """第十三次评估（V37.9.360，2026-09-24）自身的落地守卫。

    2026.9.6（09-23）= 23.5 预设的「下一个 stable」到期；判据 ② 由 🟡 回到 🔴（4.91 天）→ 日落规则新开第二十四节；
    Tool Search 默认开（#154068）经 dist 核实门控不触发我方自定义 provider，但与上游文档不一致 → 前置 C 第 14 项作预防性关闭。
    """

    def setUp(self):
        self.doc = _read()
        self.head = self.doc[:self.doc.index("## 一、版本概览")]
        self.sec24 = _slice(self.doc, "## 第二十四节", "\n---\n")
        self.sop = _slice(self.doc, "## 七、升级 SOP", "## 八、综合评估")
        self.prereq = _slice(self.sop, "### 7.0", "### 7.1")

    def test_section24_exists_with_all_subsections(self):
        for sub in ("### 24.1", "### 24.2", "### 24.3", "### 24.4", "### 24.5"):
            self.assertIn(sub, self.sec24, f"第二十四节缺 {sub}")

    def test_thirteenth_eval_heading_present_and_listed_in_head(self):
        found = [(m.group(1), m.group(2), m.group(3)) for m in _EVAL_HEADING.finditer(self.doc)]
        self.assertIn(("第二十四节", "十三", "2026-09-24"), found, found)
        self.assertIn("十三次评估", self.head)
        self.assertIn("2026.9.6", self.head, "头部评估列表须记上游 2026.9.6")
        # 「头部指向最新跟踪点」由 TestDocHeadSingleSourceOfTruth.test_head_and_prereq_point_to_latest_tracking_point
        # 动态派生守护（V37.9.366 起），本类不再写死 "第二十四节 24.5"——否则每次新评估都要改这里。

    def test_new_section_justified_not_appended(self):
        """23.5 追加条件写的是「② 仍 🟡」；本次 ② 回到 🔴 = 状态变化，须以新开节落地，不得出现 23.6 追加段。"""
        self.assertIn("日落规则", self.sec24)
        self.assertIn("新开", self.sec24)
        self.assertNotIn("### 23.6", self.doc)

    def test_criterion2_red_with_interval_and_rolling_rate(self):
        s243 = _slice(self.sec24, "### 24.3", "### 24.4")
        self.assertIn("4.91", s243)
        self.assertIn("2.0/周", s243)
        self.assertIn("🔴", s243)
        self.assertIn("DIRTY", s243)
        self.assertIn("计数仍 0", s243)
        for bad in ("9.6 干净", "计数 1", "② ✅", "节奏已收敛"):
            self.assertNotIn(bad, self.sec24, f"不得出现误记「{bad}」")

    def test_prereq_c_item14_tool_search_actionable(self):
        line = next((l for l in self.prereq.splitlines() if "#154068" in l), None)
        self.assertIsNotNone(line, "9.6 Tool Search 默认开未进 7.0 前置 C")
        self.assertIn("tools.toolSearch: false", line, "第 14 项须写出可行动的配置键")
        self.assertIn("首次启动前", line)
        self.assertIn("预防性", line, "第 14 项须诚实标注为预防性（dist 门控显示不触发）")
        self.assertEqual(self.prereq.count("- [ ]"), PREREQ_CHECKBOXES)

    def test_item14_rationale_matches_proxy_whitelist(self):
        """MR-8 跨文件契约：第 14 项的理由是「tool_search 系列名字不在 proxy 白名单 → 被剥离」。
        若未来白名单加入这三个名字，理由失效——本守卫先红，提醒复核第 14 项措辞。"""
        import proxy_filters
        for name in ("tool_search", "tool_describe", "tool_call"):
            self.assertNotIn(name, proxy_filters.ALLOWED_TOOLS, f"{name} 已进白名单，复核前置 C 第 14 项理由")
        self.assertIn("ALLOWED_TOOLS", self.prereq)

    def test_candidate_table_verdicts_match_prereq_c(self):
        s244 = _slice(self.sec24, "### 24.4", "### 24.5")
        self.assertIn("#154068", s244)
        for pr in ("#153359", "#155842"):
            self.assertIn(pr, s244, f"候选表缺 {pr}")
            self.assertNotIn(pr, self.prereq, f"登记不入的 {pr} 不得进前置 C")
        self.assertIn("登记不入", s244)

    def test_protocol_tenth_point_recorded_in_both_sections(self):
        s244 = _slice(self.sec24, "### 24.4", "### 24.5")
        for n in ("651", "2,674"):
            self.assertIn(n, s244, f"24.4 缺 9.6 协议实测数据点 {n}")
        s204 = _slice(self.doc, "### 20.4", "### 20.5")
        row = next((l for l in s204.splitlines() if l.startswith("|") and "2026.9.6" in l), None)
        self.assertIsNotNone(row, "20.4 表须追加 2026.9.6 一行")
        self.assertIn("651", row)
        self.assertIn("DIRTY", row)

    def test_tracking_point_routes_through_protocol_and_sunset_rule(self):
        s245 = _slice(self.sec24, "### 24.5", "**LAST_EVAL_DATE")
        for kw in ("19.8", "engines.node", "不新开评估节", "不重置 LAST_EVAL_DATE", "裸 grep", "[2/6]", "23.5"):
            self.assertIn(kw, s245, f"24.5 缺 {kw}")


class TestFourteenthEvaluationPresent(unittest.TestCase):
    """第十四次评估（V37.9.366，2026-10-02）自身的落地守卫。

    2026.9.7（09-30）= 24.5 预设的「下一个 stable」到期；三条判据与第十三次逐项相同（❌🔴✅），
    按 24.5 字面规则本应追加读数，但追加条件「且无新默认自主行为」不满足：Discord `allowBots` 省略时由丢弃变接收
    （#157091，dist `=== true` → `?? true`）→ 新开第二十五节，前置 C 14 → 15（预防性）；weixin 2.4.9 仍引用三个
    removal-pending SDK 子路径 → 前置 B 加核对；预注册「新默认自主行为」判定口径（代码级翻转 × 落在我方面上）。
    """

    def setUp(self):
        self.doc = _read()
        self.head = self.doc[:self.doc.index("## 一、版本概览")]
        # V37.9.373 演进（V37.9.131 alternation，镜像 V37.9.366 收紧 sec24）：原切到文末；第二十六节加入后改为切到下一个 ---，
        # 否则第十五次评估的文字会落进本类的误记黑名单扫描。第二十五节自身（25.1–25.7）的覆盖范围不变。
        self.sec25 = _slice(self.doc, "## 第二十五节", "\n---\n")
        self.sop = _slice(self.doc, "## 七、升级 SOP", "## 八、综合评估")
        self.prereq = _slice(self.sop, "### 7.0", "### 7.1")
        self.verify = _slice(self.sop, "### 7.4", "### 7.5")

    def test_section25_exists_with_all_subsections(self):
        for sub in ("### 25.1", "### 25.2", "### 25.3", "### 25.4", "### 25.5"):
            self.assertIn(sub, self.sec25, f"第二十五节缺 {sub}")

    def test_fourteenth_eval_heading_present_and_listed_in_head(self):
        found = [(m.group(1), m.group(2), m.group(3)) for m in _EVAL_HEADING.finditer(self.doc)]
        self.assertIn(("第二十五节", "十四", "2026-10-02"), found, found)
        self.assertIn("十四次评估", self.head)
        self.assertIn("2026.9.7", self.head, "头部须记上游 2026.9.7")
        self.assertIn("2026-10-02", self.head)

    def test_new_section_justified_by_default_flip_not_criteria(self):
        """三项判据全同时，新开节只能由「新默认自主行为」证成——须写明理由，且不得出现 24.6 追加段。"""
        head = self.sec25[:self.sec25.index("### 25.1")]
        for kw in ("日落规则", "且无新默认自主行为", "#157091", "新开本节"):
            self.assertIn(kw, head, f"节首触发说明缺「{kw}」")
        self.assertNotIn("### 24.6", self.doc)

    def test_criteria_unchanged_recorded_honestly(self):
        s253 = _slice(self.sec25, "### 25.3", "### 25.4")
        for kw in ("6.19", "1.5/周", "🔴", "DIRTY", "计数仍 0", "单点读数"):
            self.assertIn(kw, s253, f"25.3 缺「{kw}」")
        for bad in ("9.7 干净", "计数 1", "② ✅", "② 回 🟡", "节奏已收敛"):
            self.assertNotIn(bad, self.sec25, f"不得出现误记「{bad}」")

    def test_prereq_c_item15_discord_allowbots_actionable(self):
        line = next((l for l in self.prereq.splitlines() if "#157091" in l and l.lstrip().startswith("- [ ]")), None)
        self.assertIsNotNone(line, "9.7 Discord allowBots 默认翻转未进 7.0 前置 C")
        self.assertIn("channels.discord.allowBots: false", line, "第 15 项须写出可行动的配置键")
        self.assertIn("首次启动前", line)
        self.assertIn("预防性", line, "第 15 项须诚实标注为预防性（当前配置下不触发）")
        self.assertIn("运营配置", line, "须写明保护来自运营配置状态而非代码门控（这是入项理由）")
        # V37.9.373 演进：前置 C 头部计数随第十五次评估 15 → 18；本断言原意「头部计数已把第 15 项算进去」不变。
        m = re.search(r"\*\*(\d+) 项默认变更\*\*", self.prereq)
        self.assertIsNotNone(m, "前置 C 头部须写明默认变更项数")
        self.assertGreaterEqual(int(m.group(1)), 15)
        self.assertEqual(self.prereq.count("- [ ]"), PREREQ_CHECKBOXES)

    def test_prereq_b_weixin_removal_pending_subpaths(self):
        line = next((l for l in self.prereq.splitlines() if "前置 B" in l), "")
        for sub in ("config-runtime", "channel-message", "infra-runtime", "removal pending", "2.4.9"):
            self.assertIn(sub, line, f"前置 B 缺 weixin SDK 子路径核对要素「{sub}」")

    def test_candidate_table_verdicts_match_prereq_c(self):
        s254 = _slice(self.sec25, "### 25.4", "### 25.5")
        for pr in ("#158120", "#159548", "#160790"):
            self.assertIn(pr, s254, f"候选表缺 {pr}")
            self.assertNotIn(pr, self.prereq, f"登记不入的 {pr} 不得进前置 C")
        self.assertIn("登记不入", s254)
        self.assertIn("#157091", s254)

    def test_max_tokens_clamp_flows_into_post_upgrade_verification(self):
        """MR-8 跨节契约：25.4 登记的集成面变更（#85889）必须流进 7.4 升级后验证，而不是只活在评估节
        （V37.9.336 血案：评估结论从未回流到 SOP 清单）。"""
        self.assertIn("#85889", _slice(self.sec25, "### 25.4", "### 25.5"))
        self.assertIn("#85889", self.verify)
        self.assertIn("max_tokens", self.verify)

    def test_post_upgrade_verification_has_no_stale_item_count(self):
        """7.4 原写「复核前置 C 的 6 项默认开关」——前置 C 早已是 14 项；计数不得再硬编码在 7.4。"""
        self.assertNotRegex(self.verify, r"前置 C 的 \d+ 项")
        self.assertIn("以 7.0 清单为准", self.verify)

    def test_max_tokens_rationale_matches_adapter_passthrough(self):
        """25.4 对 #85889 的判断依据是「adapter 原样透传 Gateway 的 max_tokens」——
        若 adapter 未来改为固定 max_tokens，此登记的风险面随之改变，本守卫先红提醒复核措辞。"""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adapter.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertRegex(src, r'if "max_tokens" in body:\s*\n\s*clean\["max_tokens"\] = body\["max_tokens"\]')

    def test_protocol_eleventh_point_recorded_in_both_sections(self):
        s254 = _slice(self.sec25, "### 25.4", "### 25.5")
        for n in ("1,675", "2,923"):
            self.assertIn(n, s254, f"25.4 缺 9.7 协议实测数据点 {n}")
        s204 = _slice(self.doc, "### 20.4", "### 20.5")
        row = next((l for l in s204.splitlines() if l.startswith("|") and "2026.9.7" in l), None)
        self.assertIsNotNone(row, "20.4 表须追加 2026.9.7 一行")
        self.assertIn("1675", row)
        self.assertIn("DIRTY", row)

    def test_sunset_rule_trigger_definition_preregistered(self):
        s255 = _slice(self.sec25, "### 25.5", "**LAST_EVAL_DATE")
        for kw in ("判定口径", "代码级默认翻转", "落在我方实际使用的面", "登记不入、不触发新开节"):
            self.assertIn(kw, s255, f"25.5 预注册口径缺「{kw}」")

    def test_tracking_point_routes_through_protocol_and_sunset_rule(self):
        s255 = _slice(self.sec25, "### 25.5", "**LAST_EVAL_DATE")
        for kw in ("19.8", "engines.node", "不新开评估节", "不重置 LAST_EVAL_DATE", "裸 grep", "[2/6]", "23.5", "25.6"):
            self.assertIn(kw, s255, f"25.5 缺 {kw}")



class TestAppendReading257(unittest.TestCase):
    """2026.9.9 / 10.1 / 10.2 追加读数（V37.9.372，2026-10-12）的落地守卫。

    三个 stable 在 9 天内发布；三条判据与第十四次逐项相同（❌🔴✅），按 25.5 口径经 dist 核实无新默认自主行为
    → 按日落规则只追加 25.7，不新开第二十六节、不重置 LAST_EVAL_DATE。本节的两项新事实必须流进 SOP，
    而不是只活在评估节（V37.9.336 血案：评估结论从未回流到执行清单）：
      1) 10.1 真删除 config-runtime / channel-message / infra-runtime，weixin 2.4.10 为迁移版本 → 前置 B；
      2) 10.1 Doctor 迁移 legacy 共享根下的 WhatsApp 凭据 → 7.2 记录布局 + 7.4 核对 Doctor 输出。
    """

    def setUp(self):
        self.doc = _read()
        self.head = self.doc[:self.doc.index("## 一、版本概览")]
        # V37.9.373 演进：原切到文末；第二十六节加入后切到下一个 ---（同 sec25），25.7 自身覆盖范围不变。
        self.s257 = _slice(self.doc, "### 25.7", "\n---\n")
        self.sop = _slice(self.doc, "## 七、升级 SOP", "## 八、综合评估")
        self.prereq = _slice(self.sop, "### 7.0", "### 7.1")
        self.pre_check = _slice(self.sop, "### 7.2", "### 7.3")
        self.verify = _slice(self.sop, "### 7.4", "### 7.5")

    def test_appended_not_new_section(self):
        """三项判据全同 + 无新默认自主行为 → 追加读数：最新评估节仍是第二十五节（2026-10-02），不得出现第二十六节。"""
        # V37.9.373 演进（V37.9.131 alternation）：原断言「最新节仍是第二十五节、不存在第二十六节、头部 latest = 2026.10.2」是 25.7 当时的状态；
        # 第十五次评估新开第二十六节后改为断言 25.7 本身的形态——它是第二十五节内部的追加段，不是新节。意图不变。
        sec25 = _slice(self.doc, "## 第二十五节", "\n---\n")
        self.assertIn("### 25.7", sec25, "25.7 必须位于第二十五节内部（追加段）")
        self.assertNotIn("### 25.7", self.doc[self.doc.index("## 第二十六节"):] if "## 第二十六节" in self.doc else "")
        self.assertIn("不新开节", self.s257.splitlines()[0])
        self.assertIn("不重置 LAST_EVAL_DATE", self.s257.splitlines()[0])
        self.assertIn("10.1 / 10.2 追加读数", self.head, "头部评估列表须保留 25.7 那次追加读数")
        self.assertIn("25.7", self.head)

    def test_criteria_recorded_honestly(self):
        for kw in ("5.31", "2.55", "1.28", "🔴", "DIRTY 28", "DIRTY 1", "计数仍 0", "九版逐字相同", "1.75/周"):
            self.assertIn(kw, self.s257, f"25.7 缺「{kw}」")
        for bad in ("10.1 干净", "10.2 干净", "9.9 干净", "计数 1", "② ✅", "② 回 🟡"):
            self.assertNotIn(bad, self.s257, f"不得出现误记「{bad}」")

    def test_hotfix_branch_ambiguity_disclosed_with_invariant_conclusion(self):
        """9.9 的 hotfix 分支适用有边际；须写明结论不依赖这个选择，而不是悄悄挑一个。"""
        self.assertIn("结论不依赖这个选择", self.s257)
        self.assertIn("rc=3", self.s257)

    def test_prereq_b_requires_weixin_2_4_10_in_same_window(self):
        line = next((l for l in self.prereq.splitlines() if "前置 B" in l), "")
        for kw in ("25.7", "10.1 真删除", "2.4.10", "ERR_PACKAGE_PATH_NOT_EXPORTED", "同一升级窗口"):
            self.assertIn(kw, line, f"前置 B 缺 25.7 跟踪点兑现要素「{kw}」")

    def test_whatsapp_credential_migration_flows_into_sop(self):
        self.assertIn("credentials/whatsapp/default", self.s257)
        blocks = _bash_blocks(self.pre_check)
        self.assertTrue(any("~/.openclaw/credentials" in b for b in blocks),
                        "7.2 须有记录 WhatsApp 凭据布局的命令块")
        self.assertIn("WhatsApp 凭据迁移", self.verify)
        self.assertIn("不要删除任何一套", self.verify)

    def test_candidates_registered_not_added_to_prereq_c(self):
        for pr in ("#168164", "#162329", "#163799", "#165063"):
            self.assertIn(pr, self.s257, f"25.7 候选表缺 {pr}")
            self.assertNotIn(pr, self.prereq, f"登记不入的 {pr} 不得进前置 C")
        self.assertEqual(self.prereq.count("- [ ]"), PREREQ_CHECKBOXES)

    def test_compaction_rationale_matches_proxy_api_surface(self):
        """MR-8 跨文件：25.7 判定 #168164 不在我方面上的依据是「qwen-local 走 chat completions，不走 Responses API」。
        若 proxy 未来开始承接 /responses，OpenAI provider-native compaction 的门控可能经过我方，本守卫先红提醒复核。"""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tool_proxy.py")
        with open(path, encoding="utf-8") as f:
            code = "\n".join(l for l in f.read().splitlines() if not l.lstrip().startswith("#"))
        self.assertIn("/chat/completions", code)
        self.assertNotIn("/responses", code)

    def test_tripwire_trigger_day_readings_prepared_without_changing_machine(self):
        for kw in ("48/50", "10-19", "23.5", "主线 minor 36", "不预先改机器"):
            self.assertIn(kw, self.s257, f"25.7 缺 tripwire 触发日读数要素「{kw}」")


def _python_c_code(block):
    """从 `python3 -c "..."` 形态的命令块取出 python 源码（命令整体在双引号内、源码不含双引号）。"""
    m = re.search(r'python3 -c "(.*)"\s*$', block.strip(), re.S)
    assert m, f"命令块不是 python3 -c 形态：{block[:80]}"
    return m.group(1)


class TestFifteenthEvaluationPresent(unittest.TestCase):
    """第十五次评估（V37.9.373，2026-10-20）：tripwire [2/6] 触发日 + 2026.10.3 / 10.4 / 10.5。

    判据与第十四次逐项相同（❌🔴✅），按 25.5 口径 dist 实证一项新默认自主行为（owner-conversation automation repair，`cron` 在 PA 白名单）
    → 新开第二十六节。本类钉住：新开节理由 / 判据如实 / 25.7 更正 / 前置 C 三项新增与全量基线 / 7.2 两条只读命令与 26.4 docs 对比协议块
    真能跑出结论（行为级，从文档里抽出来跑）/ 7.4 Runtime 行核对 / tripwire 改口径的落地。
    """

    def setUp(self):
        self.doc = _read()
        self.head = self.doc[:self.doc.index("## 一、版本概览")]
        self.sec26 = _slice(self.doc, "## 第二十六节", "## 第二十七节")  # V37.9.375 起收紧到下一节（镜像 V37.9.366 sec24）
        self.sop = _slice(self.doc, "## 七、升级 SOP", "## 八、综合评估")
        self.prereq = _slice(self.sop, "### 7.0", "### 7.1")
        self.pre_check = _slice(self.sop, "### 7.2", "### 7.3")
        self.verify = _slice(self.sop, "### 7.4", "### 7.5")

    def test_section26_present_with_all_subsections(self):
        """V37.9.375 演进（第十六次评估新开第二十七节后不再是最新）：pin 改为「第二十六节仍在且标题日期不变」，意图逐字保留。"""
        heads = [(m.group(1), m.group(2), m.group(3)) for m in _EVAL_HEADING.finditer(self.doc)]
        self.assertIn(("第二十六节", "十五", "2026-10-20"), heads)
        for sub in ("### 26.1", "### 26.2", "### 26.3", "### 26.4", "### 26.5"):
            self.assertIn(sub, self.sec26, f"第二十六节缺 {sub}")
        self.assertIn("### 26.6", self.sec26, "26.6 = 同日实跑定档（V37.9.373 同日）")
        self.assertNotIn("### 26.7", self.sec26, "26.7 是下一次追加读数的位置，本次不得预先出现")
        self.assertIn("十五次评估", self.head)
        self.assertIn("10.3 / 10.4 / 10.5", self.head, "头部评估列表须记第十五次评估对象 10.3 / 10.4 / 10.5（V37.9.375 演进：latest 已移到 10.7，不再 pin 当前态行）")

    def test_new_section_justified_by_default_flip_and_trigger_day(self):
        heading = self.sec26.splitlines()[0]
        for kw in ("tripwire [2/6] 触发日", "新默认自主行为", "新开节"):
            self.assertIn(kw, heading, f"节标题须写明新开理由「{kw}」")

    def test_criteria_recorded_honestly(self):
        s263 = _slice(self.sec26, "### 26.3", "### 26.4")
        for kw in ("1.34", "2.43", "2.89", "2.25/周", "DIRTY 4", "DIRTY 1", "DIRTY 21", "计数仍 0", "十二版逐字相同", "🔴"):
            self.assertIn(kw, s263, f"26.3 缺「{kw}」")
        for bad in ("10.3 干净", "10.4 干净", "10.5 干净", "计数 1", "② ✅", "② 回 🟡"):
            self.assertNotIn(bad, self.sec26, f"不得出现误记「{bad}」")
        self.assertIn("三个条件全满足", s263, "10.4 走 hotfix 分支须写明三条件满足（不同于 9.9 的边际情形）")
        self.assertIn("61 in-range", s263)

    def test_correction_to_25_7_is_disclosed_not_rewritten(self):
        s264 = _slice(self.sec26, "### 26.4", "### 26.5")
        for kw in ("更正", "10.1", "changelog", "不改写 25.7 原文"):
            self.assertIn(kw, s264, f"26.4 更正段缺「{kw}」")
        s257 = _slice(self.doc, "### 25.7", "\n---\n")
        self.assertIn("无新默认自主行为", s257, "25.7 原文保留不改写（更正只在 26.4 追加）")

    def test_prereq_c_new_items_actionable(self):
        lines = self.prereq.splitlines()
        def item(kw):
            hit = [l for l in lines if kw in l and l.strip().startswith("- [ ]")]
            self.assertEqual(len(hit), 1, f"前置 C 须恰有一项含「{kw}」")
            return hit[0]
        repair = item("Owner-conversation automation repair")
        self.assertIn("cron.failureAlert.enabled: false", repair)
        self.assertIn("首次启动前", repair)
        self.assertIn("per-job `failureAlert`", repair, "须提醒 per-job 对象会绕过全局关闭")
        exec_item = item("Exec empty-success completion events")
        self.assertIn("tools.exec.notifyOnExitEmptySuccess: false", exec_item)
        routing = item("Multi-agent unbound inbound routing")
        for kw in ('"channel": "whatsapp"', "AgentSelectionRequiredError", "main", "不分发", "首次启动前",
                   '"default": true', "workspace-main", "26.6", "任何一段 Doctor 之前"):
            self.assertIn(kw, routing, f"第 18 项缺「{kw}」")
        self.assertNotIn("<选定 agent>", routing, "26.6 实跑后动作已定档为给 research 加标记，不再是「自选 agent 加 binding」")
        baseline = item("全量基线：")
        for kw in ("PREV=2026.4.27", "1,669", "26.4"):
            self.assertIn(kw, baseline)
        self.assertIn("**18 项默认变更**", self.prereq)
        self.assertEqual(self.prereq.count("- [ ]"), PREREQ_CHECKBOXES)

    def test_mr8_items_16_17_rationale_tracks_proxy_whitelist(self):
        """MR-8 跨文件：第 16 / 17 项入清单的理由是「cron / exec 在 PA 白名单」。白名单若移除它们，理由失效，本守卫先红提醒复核。"""
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import proxy_filters
        for name in ("cron", "exec"):
            self.assertIn(name, proxy_filters.ALLOWED_TOOLS, f"{name} 已不在白名单，复核前置 C 第 16 / 17 项理由")

    def test_mr8_registered_not_included_rationale_tracks_adapter_body(self):
        """MR-8 跨文件：reasoning_effort / stream_options 登记不入的依据是 adapter 以白名单重建请求体。adapter 若开始透传它们，本守卫先红。"""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adapter.py")
        with open(path, encoding="utf-8") as f:
            code = "\n".join(l for l in f.read().splitlines() if not l.lstrip().startswith("#"))
        self.assertIn('clean = {"model": use_model, "messages": clean_msgs}', code)
        for key in ("reasoning_effort", "stream_options"):
            self.assertNotIn(key, code, f"adapter 开始处理 {key}，复核 26.4 登记不入的结论")

    def _run_cmd(self, cmd, home):
        env = os.environ.copy()
        env["HOME"] = home
        r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, env=env, timeout=30)
        return r

    def test_pre_check_commands_run_and_show_conclusion(self):
        """7.2 的两条只读命令从文档抽出来真跑（原则 #35 补充：交出去的验证命令要先证明能显出结论）。"""
        blocks = [b for b in _bash_blocks(self.pre_check) if "openclaw.json" in b or "cron/jobs.json" in b]
        self.assertEqual(len(blocks), 2, "7.2 须有 agents/bindings 与 cron 任务两条命令")
        for b in blocks:
            compile(_python_c_code(b), "<7.2>", "exec")
        tmp = tempfile.mkdtemp(prefix="sop72_")
        os.makedirs(os.path.join(tmp, ".openclaw", "cron"))
        with open(os.path.join(tmp, ".openclaw", "openclaw.json"), "w") as f:
            json.dump({"agents": {"list": [{"id": "research"}, {"id": "main", "default": True}]},
                       "bindings": [{"agentId": "main", "match": {"channel": "whatsapp"}}]}, f)
        with open(os.path.join(tmp, ".openclaw", "cron", "jobs.json"), "w") as f:
            json.dump({"jobs": [{"id": "j1", "name": "water", "enabled": True, "delivery": {"mode": "announce"},
                                 "sessionKey": "agent:main:main"}]}, f)
        agents_cmd = next(b for b in blocks if "openclaw.json" in b)
        out = self._run_cmd(agents_cmd, tmp)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("agent: research default: None", out.stdout)
        self.assertIn("agent: main default: True", out.stdout)
        self.assertIn('"channel": "whatsapp"', out.stdout)
        cron_cmd = next(b for b in blocks if "cron/jobs.json" in b)
        out = self._run_cmd(cron_cmd, tmp)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("jobs: 1", out.stdout)
        self.assertIn("delivery: announce", out.stdout)
        self.assertIn("session: agent:main:main", out.stdout)
        empty = tempfile.mkdtemp(prefix="sop72e_")
        out = self._run_cmd(cron_cmd, empty)
        self.assertEqual((out.returncode, out.stdout.strip()), (0, "jobs: 0"), "没有 cron 文件时须如实报 0，不得报错")

    def test_post_upgrade_runtime_line_check_present(self):
        self.assertIn("_capture_conversation_turn", self.verify)
        self.assertTrue(any("Runtime: (name|agent|session|host)=" in b and "~/.kb/conversations/" in b
                            for b in _bash_blocks(self.verify)), "7.4 须有 Runtime 行核对命令")
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tool_proxy.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("def _capture_conversation_turn(", src, "7.4 引用的函数名须真实存在")
        self.assertIn('_CHAT_LOG_DIR = os.path.expanduser("~/.kb/conversations")', src, "7.4 命令的路径须与捕获目录一致")

    def _docs_protocol_python(self):
        s264 = _slice(self.sec26, "### 26.4", "### 26.5")
        block = next(b for b in _bash_blocks(s264) if "<<'PYDOC'" in b)
        body = block[block.index("<<'PYDOC'\n") + len("<<'PYDOC'\n"):block.rindex("\nPYDOC")]
        return block, body

    def test_docs_protocol_block_compiles_and_is_whole_doc_scan(self):
        block, body = self._docs_protocol_python()
        compile(body, "<26.4>", "exec")
        self.assertIn("npm pack openclaw@$PREV openclaw@$TARGET", block)
        self.assertIn('startswith("package/docs/")', body, "须扫全部自带 docs，而不是某一个文件")
        self.assertIn("not in set(old.get(n, []))", body, "只取新增行（与旧版逐文件对比）")

    def _make_pkg(self, root, version, files):
        import io
        import tarfile
        path = os.path.join(root, f"openclaw-{version}.tgz")
        with tarfile.open(path, "w:gz") as t:
            for name, text in files.items():
                data = text.encode("utf-8")
                info = tarfile.TarInfo(f"package/docs/{name}")
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
            pj = b'{"name": "openclaw"}'
            info = tarfile.TarInfo("package/package.json")
            info.size = len(pj)
            t.addfile(info, io.BytesIO(pj))

    def test_docs_protocol_behavior_on_synthetic_packages(self):
        """行为级：新增的默认值句子被抓到、落在我方面上的加 ★、未变化的默认值句子不报。"""
        _, body = self._docs_protocol_python()
        tmp = tempfile.mkdtemp(prefix="docsproto_")
        self._make_pkg(tmp, "1.0.0", {"a.md": "Bar defaults to 3.\nintro"})
        self._make_pkg(tmp, "1.1.0", {"a.md": "Bar defaults to 3.\nintro\nFoo is on by default.",
                                      "automation/cron.md": "Repair is on by default for cron jobs."})
        env = os.environ.copy()
        env.update({"PREV": "1.0.0", "TARGET": "1.1.0"})
        r = subprocess.run([sys.executable, "-c", body], cwd=tmp, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("added_default_lines=2 on_our_surface=1", r.stdout)
        self.assertIn("★ automation/cron.md: Repair is on by default", r.stdout)
        self.assertIn("  a.md: Foo is on by default.", r.stdout)
        self.assertNotIn("Bar defaults to 3", r.stdout, "两版都有的默认值句子不是新增，不得报")
        self._make_pkg(tmp, "0.9.0", {})
        env.update({"PREV": "0.9.0"})
        r = subprocess.run([sys.executable, "-c", body], cwd=tmp, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 3, "某一版没有自带 docs 时须判不可判（rc=3），不得当作零候选")
        self.assertIn("VERDICT=N/A_NO_DOCS", r.stdout)

    def test_tripwire_caliber_decision_landed_in_doc_and_script(self):
        s265 = _slice(self.sec26, "### 26.5", "**LAST_EVAL_DATE")
        for kw in ("改口径", "`MAINT_PATCH_MIN` = 32", "52", "49", "39", "11 月下旬", "5.28"):
            self.assertIn(kw, s265, f"26.5 缺口径决定要素「{kw}」")
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "check_upgrade.sh")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertIn('MAINT_PATCH_MIN="${OPENCLAW_MAINT_PATCH_MIN:-32}"', src)
        self.assertIn("主线 stable", src)

    def test_next_tracking_point_includes_docs_candidates(self):
        s265 = _slice(self.sec26, "### 26.5", "**LAST_EVAL_DATE")
        for kw in ("19.8", "engines.node", "26.7 追加读数", "docs 默认值增量对比", "不新开评估节", "不重置 LAST_EVAL_DATE", "裸 grep", "18 项"):
            self.assertIn(kw, s265, f"26.5 跟踪点缺「{kw}」")

    def test_protocol_table_rows_appended(self):
        t = _slice(self.doc, "### 20.4", "### 20.5")
        for v in ("2026.10.3", "2026.10.4", "2026.10.5"):
            self.assertIn(f"| **{v}**（第十五次评估追加）", t)


class TestSixteenthEvaluationPresent(unittest.TestCase):
    """第十六次评估（V37.9.375，2026-10-27）：2026.10.6 / 10.7 两个 stable 触发。

    判据 ① / ② 与第十五次相同，但 ③ 由 ✅ 变 ❌——10.6 起 engines.node 收紧为 `>=24.22.1 <25 || >=26.12.1`
    （Node 2026-10 安全发布，非 SQLite），Mac Mini 26.5.0 落到区间外 → 按日落规则新开第二十七节。
    本类钉住：新开节理由 / 判据如实（含 ③ 的上游、我方两半）/ 新区间逐字进 7.0 前置 A 并带可行动句（MR-8 跨节）/
    七个门控函数复查与「无新默认自主行为」/ 26.6 升级路径文档对比已执行 / 前置 B 复查 / 20.4 表行 / 跟踪点 /
    27.5 的 Node 核对命令从文档抽出来用假 node 两向真跑（原则 #35 补充）。
    """

    NODE_RANGE = ">=24.22.1 <25 || >=26.12.1"

    def setUp(self):
        self.doc = _read()
        self.head = self.doc[:self.doc.index("## 一、版本概览")]
        self.sec27 = self.doc[self.doc.index("## 第二十七节"):]
        self.sop = _slice(self.doc, "## 七、升级 SOP", "## 八、综合评估")
        self.prereq = _slice(self.sop, "### 7.0", "### 7.1")

    def test_section27_is_latest_with_all_subsections(self):
        self.assertEqual(_latest_eval(self.doc), ("第二十七节", "十六", "2026-10-27"))
        for sub in ("### 27.1", "### 27.2", "### 27.3", "### 27.4", "### 27.5"):
            self.assertIn(sub, self.sec27, f"第二十七节缺 {sub}")
        self.assertNotIn("### 27.6", self.sec27, "27.6 是下一次追加读数的位置，本次不得预先出现")
        self.assertIn("十六次评估", self.head)
        self.assertIn("2026.10.7", self.head, "头部当前态须记上游 latest 2026.10.7")

    def test_new_section_justified_by_criterion3_change(self):
        heading = self.sec27.splitlines()[0]
        for kw in ("判据 ③", "✅ 变 ❌", "26.12.1", "26.5.0", "新开节"):
            self.assertIn(kw, heading, f"节标题须写明新开理由「{kw}」")

    def test_criteria_recorded_honestly(self):
        s273 = _slice(self.sec27, "### 27.3", "### 27.4")
        for kw in ("3.64", "1.79", "2.5/周", "DIRTY 4", "DIRTY 1", "计数仍 0", "26.5.0", "26.12.1", "🔴", "区间外", "由 ✅ 变",
                   self.NODE_RANGE, "21 retained seed-only", "293 in-range"):
            self.assertIn(kw, s273, f"27.3 缺「{kw}」")
        for bad in ("10.6 干净", "10.7 干净", "计数 1", "② ✅", "③ ✅", "② 回 🟡", "与第十五次逐项相同", "判据未变"):
            self.assertNotIn(bad, self.sec27, f"不得出现误记「{bad}」")
        self.assertIn("❌🔴❌", self.sec27, "结论行须如实记 ③ 为 ❌")

    def test_criterion3_root_cause_is_security_not_sqlite_with_enforcement_points(self):
        """③ 这次的根因与执行点决定了动作的紧迫性，三处执行点都须写明。"""
        s273 = _slice(self.sec27, "### 27.3", "### 27.4")
        for kw in ("assertSupportedRuntime", "openclaw update", "Gateway 运行时选择"):
            self.assertIn(kw, s273, f"27.3 缺执行点「{kw}」")
        s271 = _slice(self.sec27, "### 27.1", "### 27.2")
        for kw in ("不是 SQLite", "eMudhra", "security"):
            self.assertIn(kw, s271, f"27.1 缺根因要素「{kw}」")

    def test_node_range_10_6_present_in_sop_prereq_a_with_action(self):
        """MR-8 跨节：10.6 的新区间须逐字进 7.0 前置 A，且带「26.5.0 在区间外 → brew upgrade node」可行动句。"""
        norm = lambda t: t.replace("\\|", "|").replace(" ", "")
        self.assertIn(norm(self.NODE_RANGE), norm(self.prereq), "7.0 前置 A 未携带 10.6 起的 node 区间")
        lines = [l for l in self.prereq.splitlines() if "前置 A" in l and l.strip().startswith("- [ ]")]
        self.assertEqual(len(lines), 1, "前置 A 须恰有一项")
        a = lines[0]
        for kw in ("26.5.0", "区间外", "brew upgrade node", "26.12.1", "不等开窗", "assertSupportedRuntime", "拒绝启动", "27.5"):
            self.assertIn(kw, a, f"前置 A 缺「{kw}」")
        self.assertIn("五版未变", a, "历史「9.3–9.7 五版未变」记录保留（10.6 之前的事实不改写，只追加）")

    def test_prereq_c_unchanged_and_gates_recomputed(self):
        s274 = _slice(self.sec27, "### 27.4", "### 27.5")
        for fn in ("resolveAgentToolSearchRuntimeConfig", "allowBots", "allowAcrossProviders", "failure-repair",
                   "resolveNotifyOnExitEmptySuccess", "resolveAgentRoute", "relocateNonBehavioralRegion"):
            self.assertIn(fn, s274, f"27.4 须记录门控函数「{fn}」的复查结果")
        self.assertIn("无新默认自主行为", s274)
        self.assertIn("保持 18 项", s274)
        self.assertIn("**18 项默认变更**", self.prereq)
        self.assertEqual(self.prereq.count("- [ ]"), PREREQ_CHECKBOXES, "本次零新增前置项，清单计数不得变")

    def test_registered_not_included_session_tool_rationale_tracks_whitelist(self):
        """MR-8 跨文件：session 工具 ★ 候选登记不入的依据是白名单里的会话工具名在 10.7 仍存在。白名单若改名，本守卫先红提醒复核。"""
        s274 = _slice(self.sec27, "### 27.4", "### 27.5")
        self.assertIn('action: "stop"', s274)
        self.assertIn("登记不入", s274)
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import proxy_filters
        for name in ("sessions_spawn", "sessions_send", "sessions_history"):
            self.assertIn(name, proxy_filters.ALLOWED_TOOLS, f"{name} 已不在白名单，复核 27.4 登记不入的结论")
            self.assertIn(name, s274)

    def test_upgrade_path_docs_comparison_executed_and_prereq_b_rechecked(self):
        """26.6 方法补丁首次执行：升级路径文档整份对比须有结论（桥接版本未变）；前置 B 复查须记 exports 零删除与 weixin 回退分支。"""
        s274 = _slice(self.sec27, "### 27.4", "### 27.5")
        for kw in ("桥接版本未变（2026.9.5）", "updating.md", "--full-integrity-check", "update status", "不改 7.3"):
            self.assertIn(kw, s274, f"27.4 升级路径文档对比缺「{kw}」")
        for kw in ("零删除", "path-existence-runtime", "2.4.10", "ERR_PACKAGE_PATH_NOT_EXPORTED", "前置 B 不变"):
            self.assertIn(kw, s274, f"27.4 前置 B 复查缺「{kw}」")
        prereq_d = [l for l in self.prereq.splitlines() if "前置 D" in l]
        self.assertTrue(prereq_d and "2026.9.5" in prereq_d[0], "前置 D 桥接版本须仍为 2026.9.5（27.4 结论「未变」的另一半）")

    def test_protocol_table_rows_appended(self):
        t = _slice(self.doc, "### 20.4", "### 20.5")
        for v in ("2026.10.6", "2026.10.7"):
            self.assertIn(f"| **{v}**（第十六次评估追加）", t)
        self.assertIn("| **2026.10.7**（第十六次评估追加） | **13** | 1 | 69 | **14** | **62** |", t)

    def test_tracking_point_routes_through_protocol_and_sunset_rule(self):
        s275 = _slice(self.sec27, "### 27.5", "**LAST_EVAL_DATE")
        for kw in ("19.8", "engines.node", "27.6 追加读数", "docs 默认值增量对比", "升级路径文档整份对比", "不新开评估节",
                   "不重置 LAST_EVAL_DATE", "裸 grep", "18 项", "node -v", "两半", "第九次再证"):
            self.assertIn(kw, s275, f"27.5 跟踪点缺「{kw}」")
        self.assertIn("LAST_EVAL_DATE 更新至 2026-10-27", self.sec27)

    def _node_check_block(self):
        s275 = _slice(self.sec27, "### 27.5", "**LAST_EVAL_DATE")
        blocks = [b for b in _bash_blocks(s275) if b.startswith("node -v")]
        self.assertEqual(len(blocks), 1, "27.5 须有恰一条 Node 区间核对命令")
        block = blocks[0]
        self.assertFalse(any(l.lstrip().startswith("#") for l in block.splitlines()), "原则 #35：命令块无注释行")
        return block

    def _run_with_fake_node(self, block, version):
        bindir = tempfile.mkdtemp(prefix="fakenode_")
        with open(os.path.join(bindir, "node"), "w") as f:
            f.write(f"#!/bin/sh\necho {version}\n")
        os.chmod(os.path.join(bindir, "node"), 0o755)
        env = os.environ.copy()
        env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
        return subprocess.run(["bash", "-c", block], capture_output=True, text=True, env=env, timeout=30)

    def test_node_check_command_runs_and_shows_conclusion(self):
        """行为级（原则 #35 补充）：27.5 的核对命令从文档抽出来用假 node 跑——26.5.0 须判区间外 rc=1，26.12.1 / 24.22.1 在区间 rc=0，24.16.0 / 25.9.0 区间外。"""
        block = self._node_check_block()
        for ver, ok in (("v26.5.0", False), ("v26.12.1", True), ("v26.13.0", True), ("v24.22.1", True),
                        ("v24.16.0", False), ("v25.9.0", False), ("v24.99.0", True)):
            r = self._run_with_fake_node(block, ver)
            self.assertEqual(r.returncode, 0 if ok else 1, f"{ver}: rc={r.returncode} out={r.stdout!r} err={r.stderr!r}")
            self.assertIn("在区间" if ok else "区间外", r.stdout, f"{ver}: 结论须可读")
            self.assertIn(ver.lstrip("v"), r.stdout, f"{ver}: 输出须回显实测版本")


class TestSameDayRunbook266(unittest.TestCase):
    """V37.9.373 同日 26.6：Mac Mini 实测 agents.list = research / ops / main 无标记 → dev 用 9.5 / 10.5 真 Doctor 实跑。

    两件事：(1) 上游要求 7 月前的安装先过桥接版本（2026.9.5），此前四次评估都没核到 → 前置 D + 7.3 两段式；
    (2) 不加标记时经桥接 WhatsApp 落到 main、工作区换成 workspace-main → 第 18 项定档为给 research 加 `default: true`。
    本类钉住两件事在 SOP 里的落点，并把 7.2 / 7.4 的核对命令从文档里抽出来对 Doctor 实跑出的两种配置形状真跑。
    """

    def setUp(self):
        self.doc = _read()
        self.sec26 = _slice(self.doc, "## 第二十六节", "## 第二十七节")  # V37.9.375 起收紧到下一节（镜像 V37.9.366 sec24）
        self.s266 = self.sec26[self.sec26.index("### 26.6"):]
        self.sop = _slice(self.doc, "## 七、升级 SOP", "## 八、综合评估")
        self.prereq = _slice(self.sop, "### 7.0", "### 7.1")
        self.pre_check = _slice(self.sop, "### 7.2", "### 7.3")
        self.install = _slice(self.sop, "### 7.3", "### 7.4")
        self.verify = _slice(self.sop, "### 7.4", "### 7.5")

    def _run(self, cmd, home):
        env = os.environ.copy()
        env["HOME"] = home
        return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, env=env, timeout=30)

    def _home_with(self, cfg):
        home = tempfile.mkdtemp(prefix="sop266_")
        os.makedirs(os.path.join(home, ".openclaw"))
        with open(os.path.join(home, ".openclaw", "openclaw.json"), "w") as f:
            json.dump(cfg, f)
        return home

    def test_section_266_records_upstream_bridge_with_versions(self):
        for kw in ("July 1, 2026", "2026.9.5", "2026.9.7", "left unchanged", "Current releases leave those retired state files untouched",
                   "9.4 / 9.5 / 9.6", "9.7 / 9.8 / 9.9", "10.1 / 10.2 / 10.5", "2026-04-27"):
            self.assertIn(kw, self.s266, f"26.6 缺桥接要素「{kw}」")

    def test_section_266_records_method_patch_and_correction(self):
        for kw in ("方法补丁", "docs/install/updating", "docs/gateway/doctor", "为什么漏了四次", "更正", "26.4 表行已加注"):
            self.assertIn(kw, self.s266, f"26.6 缺「{kw}」")
        row = next(l for l in self.sec26.splitlines() if l.startswith("| 多 agent 入站无 binding"))
        self.assertIn("26.6 实跑补全", row, "26.4 表行须加注指向 26.6")

    def test_section_266_table_covers_all_paths(self):
        for kw in ("经桥接（9.5 → 10.5），不加标记", "经桥接，research 加 `default: true`", "直跳 10.5，不加标记", "直跳 10.5，加标记", "不跑 Doctor",
                   "`~/.openclaw/workspace-main`", "AgentSelectionRequiredError", "matchedBy: default", "binding.channel"):
            self.assertIn(kw, self.s266, f"26.6 实跑表缺「{kw}」")
        self.assertIn("诚实边界", self.s266)

    def test_prereq_d_two_stage_actionable(self):
        lines = [l for l in self.prereq.splitlines() if l.strip().startswith("- [ ] **前置 D")]
        self.assertEqual(len(lines), 1, "7.0 须恰有一项前置 D")
        d = lines[0]
        for kw in ("2026-07-01", "2026.9.5", "2026-04-27", "Gateway 保持停止", "`npm install -g`", "`openclaw update`", "第 18 项", "26.6"):
            self.assertIn(kw, d, f"前置 D 缺「{kw}」")
        self.assertEqual(self.prereq.count("- [ ]"), PREREQ_CHECKBOXES)

    def test_install_step_goes_through_bridge_before_target(self):
        """7.3 两段式顺序：桥接版本 → doctor --fix → 目标版本 → doctor --fix。"""
        blocks = _bash_blocks(self.install)
        seq = []
        for b in blocks:
            if "openclaw@<桥接版本>" in b:
                seq.append("bridge")
            if "openclaw@<目标版本>" in b:
                seq.append("target")
            if "openclaw doctor --fix" in b:
                seq.append("doctor")
        self.assertEqual(seq, ["bridge", "doctor", "target", "doctor"], f"7.3 顺序不对：{seq}")
        self.assertIn("--allow-scripts=openclaw", self.install)

    def test_pre_check_prose_matches_command_output_for_marker(self):
        """7.2 正文写的「加好标记后应显示 agent: research default: True」必须真是那条命令的输出（文档说的与命令打的一致）。"""
        self.assertIn("`agent: research default: True`", self.pre_check)
        cmd = next(b for b in _bash_blocks(self.pre_check) if "openclaw.json" in b)
        home = self._home_with({"agents": {"list": [{"id": "research", "default": True}, {"id": "ops"}, {"id": "main"}]}})
        out = self._run(cmd, home)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("agent: research default: True", out.stdout)

    def test_post_doctor_ownership_check_runs_and_shows_conclusion(self):
        """7.4 归属核对命令抽出来，对 26.6 实跑得到的两种 Doctor 结果形状真跑：标记生效 / 标记缺失，两种都要能一眼看出。"""
        cmds = [b for b in _bash_blocks(self.verify) if "systemAgent" in b and "entries" in b]
        self.assertEqual(len(cmds), 1, "7.4 须有一条 agent 归属核对命令")
        compile(_python_c_code(cmds[0]), "<7.4>", "exec")
        ws = "/Users/x/.openclaw/workspace"
        good = self._home_with({"agents": {"ownership": "explicit",
                                           "defaults": {"systemAgent": {"agentId": "research"}, "heartbeat": {"agentId": "research"}},
                                           "entries": {"research": {"workspace": ws}, "ops": {}, "main": {}}},
                                "bindings": [{"agentId": "research", "match": {"channel": "whatsapp", "accountId": "*"}}]})
        out = self._run(cmds[0], good)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("systemAgent: research heartbeat: research", out.stdout)
        self.assertIn("agent: research workspace: " + ws, out.stdout)
        self.assertIn('binding: research {"channel": "whatsapp", "accountId": "*"}', out.stdout)
        bad = self._home_with({"agents": {"ownership": "explicit",
                                          "defaults": {"systemAgent": {"agentId": "main"}, "heartbeat": {"agentId": "main"}},
                                          "entries": {"research": {"workspace": ws}, "ops": {}, "main": {}}}})
        out = self._run(cmds[0], bad)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("systemAgent: main heartbeat: main", out.stdout)
        self.assertNotIn("binding:", out.stdout)
        for kw in ("systemAgent: research heartbeat: research", "`systemAgent: main`", "Gateway 先不要启动"):
            self.assertIn(kw, self.verify, f"7.4 正文须写明判读「{kw}」")

    def test_plan_b_and_c_reconciled_with_bridge(self):
        s265 = _slice(self.sec26, "### 26.5", "**LAST_EVAL_DATE")
        self.assertIn("前置 D 的桥接版本只过不停", s265)
        self.assertIn("**两段式（前置 D，26.6）**", s265)
        self.assertIn("26.6", self.doc[:self.doc.index("## 一、版本概览")], "头部十五次评估行须提到同日 26.6")


if __name__ == "__main__":
    unittest.main(verbosity=2)
