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
import os
import re
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
        for kw in ("六次评估", "七次评估", "八次评估"):
            self.assertIn(kw, self.head, f"头部评估列表缺 {kw}")

    def test_overview_table_marked_point_in_time(self):
        tbl = _slice(self.doc, "## 一、版本概览", "## 二、原 Hold 条件评估")
        self.assertIn("时点快照", tbl,
                      "版本概览表是 2026-04 快照，必须显式标注")
        self.assertNotIn("| 当前部署版本 |", tbl,
                         "概览表不得再声称『当前部署版本』（陈旧 4 个月）")

    def test_section8_marked_point_in_time(self):
        s8 = _slice(self.doc, "## 八、综合评估", "## 九")
        self.assertIn("时点快照", s8)
        self.assertIn("第十九节", s8, "第八节须指向最新评估结论")


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
        """check_upgrade.sh 的 LAST_EVAL_DATE 必须与第八次评估日期一致。"""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "check_upgrade.sh")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        m = re.search(r'LAST_EVAL_DATE="\$\{OPENCLAW_LAST_EVAL_DATE:-([\d-]+)\}"', src)
        self.assertIsNotNone(m, "check_upgrade.sh 未找到 LAST_EVAL_DATE 默认值")
        self.assertEqual("2026-09-01", m.group(1),
                         "第八次评估完成后 LAST_EVAL_DATE 必须重置")
        self.assertIn("第十九节", src, "注释须指向最新评估节")


if __name__ == "__main__":
    unittest.main(verbosity=2)
