#!/usr/bin/env python3
"""V37.9.320（对抗审计 · 从未覆盖的三个面）守卫.

镜头 = 「证据机制与防护机制坏了, 我们发现得了吗」。三个此前从未被审过的面,
逐条 grounded 探针复现（实录见 changelog V37.9.320）:

  AL-1 (文档过度声称) audit_log 的「删除可检测」只对**中间删除**成立。实测:
       6 条删中间第 3 条 → 1 error 被抓; 6 条**删尾部 2 条 → ok=True/errors=0**。
       这是本地追加式哈希链的固有属性(无外部锚点无法证明"后面还该有内容"),
       按日落法不建锚点机制, 只如实记录能力边界。
  AL-2 (MED, fail-plausible) 日志**整个文件被删** → verify 返回 ok=True/total=0,
       full_regression 打「✅ (0 records)」—— **销毁全部证据得到最令人安心的输出**。
  SS-1 (MED, 空转评分) security_score 的「审计追踪」15 分全部来自在源码里 grep
       字符串, **从不调用 verify_chain 也不看日志是否存在**。实测: 审计日志完全
       不存在时仍得 15/15 + 三个绿 ✅。而 98/100 被推到徽章/status.json/周报。
  EE-2 (MED, 静默无限计费) expert_escalation 配额的执行**完全依赖**审计写入落盘
       (check_daily_quota 数日志行数), 而 write_audit_record 是 FAIL-OPEN。实测:
       审计路径不可写时 **50 次调用全部放行**(上限 30), 唯一信号是 50 条 stderr WARN,
       而每次调用都真实付费。

诚实剔除(验证后判定不是 bug, 登记防重复调查):
  · expert quota 用 UTC 日期 —— timestamp_iso 读写**都是 UTC, 内部一致**,
    唯一影响是配额日界在 08:00 HKT 而非午夜, 无害。
  · _DANGEROUS_TOKENS 子串匹配 —— 多字符特定串(rm -rf / curl | bash),
    且对安全守卫而言子串是**保守**方向(误挡 > 漏放)。
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import shutil
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit_log            # noqa: E402
import security_score       # noqa: E402
import expert_escalation as ee  # noqa: E402

_REPO = os.path.dirname(os.path.abspath(__file__))


def _read(name):
    with open(os.path.join(_REPO, name), encoding="utf-8") as f:
        return f.read()


class _IsolatedAuditFile(unittest.TestCase):
    """把 audit_log.AUDIT_FILE 指到临时目录, 绝不碰真实 ~/.kb/audit.jsonl (MR-9)。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = audit_log.AUDIT_FILE
        audit_log.AUDIT_FILE = os.path.join(self.tmp, "audit.jsonl")

    def tearDown(self):
        audit_log.AUDIT_FILE = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, n):
        for i in range(n):
            audit_log.audit("claude_code", "set", f"t{i}", f"记录 {i}")
        with open(audit_log.AUDIT_FILE, encoding="utf-8") as f:
            return f.read().splitlines()


class TestAL2MissingLogDistinguishable(_IsolatedAuditFile):
    """AL-2: 「日志不存在」必须能与「已校验通过」区分。"""

    def test_missing_log_reports_exists_false(self):
        r = audit_log.verify_chain()          # 文件尚未创建
        self.assertTrue(r["ok"], "ok 语义刻意不改（改了 dev CI 立刻挂）")
        self.assertEqual(r["total"], 0)
        self.assertFalse(r["exists"], "销毁全部证据不得与「已校验」同形")

    def test_present_log_reports_exists_true(self):
        self._seed(3)
        r = audit_log.verify_chain()
        self.assertTrue(r["ok"])
        self.assertEqual(r["total"], 3)
        self.assertTrue(r["exists"])

    def test_full_regression_presents_missing_distinctly(self):
        src = _read("full_regression.sh")
        self.assertIn("AUDIT_EXISTS", src)
        self.assertIn("审计日志不存在", src)
        # 退役: 无条件对 0 条记录打 ✅
        self.assertNotIn('echo "✅ ($AUDIT_COUNT records)"\n    PASS=', src)


class TestAL1DetectionBoundaryIsReal(_IsolatedAuditFile):
    """AL-1: 把实测的检测能力边界钉死, 防未来把过度声称写回文档。"""

    def test_middle_deletion_is_detected(self):
        lines = self._seed(6)
        with open(audit_log.AUDIT_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines[:2] + lines[3:]) + "\n")
        r = audit_log.verify_chain()
        self.assertFalse(r["ok"], "中间删除必须被抓（prev 指针断裂）")

    def test_content_tamper_is_detected(self):
        self._seed(3)
        with open(audit_log.AUDIT_FILE, encoding="utf-8") as f:
            lines = f.read().splitlines()
        e = json.loads(lines[1]); e["summary"] = "篡改后的摘要"
        lines[1] = json.dumps(e, ensure_ascii=False)
        with open(audit_log.AUDIT_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        self.assertFalse(audit_log.verify_chain()["ok"], "内容篡改必须被抓")

    def test_tail_truncation_is_NOT_detected_and_documented(self):
        """🔴 诚实边界: 尾部截断检测不到 —— 断言现状而非假装能抓。

        若未来有人实现了外部锚点让它可检测, 本测试会失败, 那是**好事**:
        届时同步更新 docstring 的能力边界表即可。
        """
        lines = self._seed(6)
        with open(audit_log.AUDIT_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines[:-2]) + "\n")
        r = audit_log.verify_chain()
        self.assertTrue(r["ok"], "尾部截断当前检测不到（本地哈希链固有属性）")
        self.assertEqual(r["total"], 4)
        # 文档必须如实登记该边界, 不得写"删除可检测"了事
        doc = _read("audit_log.py")
        self.assertIn("尾部截断", doc)
        self.assertIn("固有属性", doc)


class TestSS1AuditDimensionHonesty(unittest.TestCase):
    """SS-1: 静态评分不得把「代码写了」表述成「机制在工作」。"""

    def test_details_do_not_assert_runtime_behavior(self):
        score, total, details = security_score.check_audit_trail()
        self.assertEqual((score, total), (15, 15))   # 现状: 满分（源码齐全）
        joined = " ".join(details)
        self.assertIn("源码", joined, "详情必须说明所测的是源码而非运行时")
        # 退役: 断言行为的旧文案
        self.assertNotIn("✅ 链式哈希审计日志", joined)
        self.assertNotIn("✅ 审计完整性自动校验", joined)

    def test_scope_statement_next_to_score(self):
        data = security_score.compute_score()
        report = security_score.format_report(data)
        head = "\n".join(report.splitlines()[:3])
        self.assertIn("静态姿态", head, "范围声明必须紧贴分数, 否则数字仍会被读成'系统有多安全'")

    def test_dimension_still_static_by_design_documented(self):
        """刻意不加运行时探测（会重新引入 V37.9.268 解耦掉的环境敏感）—— 留痕。"""
        src = _read("security_score.py")
        self.assertIn("刻意不加运行时探测", src)
        self.assertIn("V37.9.268", src)
        # 防空转: 该维度确实没有调 verify_chain。
        # 只扫**可执行行** —— 本次首写守卫时被自己 docstring 里的
        # "从不调用 audit_log.verify_chain()" 咬中（V37.9.178 家族, 当日第三次）。
        i = src.index("def check_audit_trail")
        j = src.index("def check_availability")
        body = src[i:j]
        # 去掉函数 docstring（第一个 '''"""''' 到下一个 '''"""'''）
        d1 = body.index('"""'); d2 = body.index('"""', d1 + 3)
        code = body[:d1] + body[d2 + 3:]
        code_lines = [ln for ln in code.splitlines() if not ln.lstrip().startswith("#")]
        self.assertNotIn("verify_chain", "\n".join(code_lines))
        # 防空转的防空转: 切出来的可执行体必须确实含该维度的判分逻辑
        self.assertIn("score += 5", "\n".join(code_lines))


class TestEE2QuotaFailsClosedWhenUnauditable(unittest.TestCase):
    """EE-2: 记不了账就不许花钱。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _unwritable_path(self):
        """root 也绕不过: 在普通文件下面建子目录 → NotADirectoryError。"""
        blocker = os.path.join(self.tmp, "afile")
        with open(blocker, "w") as f:
            f.write("x")
        return os.path.join(blocker, "sub", "expert_audit.jsonl")

    def test_writable_path_ok(self):
        ok, reason = ee.audit_log_writable(os.path.join(self.tmp, "a", "audit.jsonl"))
        self.assertTrue(ok, reason)

    def test_unwritable_path_detected(self):
        ok, reason = ee.audit_log_writable(self._unwritable_path())
        self.assertFalse(ok)
        self.assertTrue(reason)

    def test_empty_path_treated_unwritable(self):
        ok, _ = ee.audit_log_writable("")
        self.assertFalse(ok)

    def test_escalate_refuses_when_audit_unwritable(self):
        """血案回归: 审计写不进去时曾静默放行无限次付费调用。"""
        r = ee.escalate("测试问题", audit_log_path=self._unwritable_path())
        self.assertEqual(r["status"], "audit_unavailable",
                         f"审计不可写时必须拒绝调用, 实得: {r.get('status')}")
        self.assertIn("配额", r["error"])

    def test_probe_writes_nothing(self):
        """探针不得污染审计日志（否则每次调用凭空多一条空记录）。"""
        p = os.path.join(self.tmp, "probe", "audit.jsonl")
        ee.audit_log_writable(p)
        self.assertTrue(os.path.exists(p))
        with open(p, encoding="utf-8") as f:
            self.assertEqual(f.read(), "", "探针只应 touch, 不应写内容")

    def test_probe_runs_before_paid_call(self):
        """源码守卫: 探针必须在加载上下文/调 API 之前（花钱前先确认能记账）。"""
        src = _read("expert_escalation.py")
        i_probe = src.index("_audit_ok, _audit_reason = audit_log_writable")
        i_ctx = src.index("status_data = load_status(kb_dir)")
        self.assertLess(i_probe, i_ctx, "记账探针必须先于上下文加载与 API 调用")

    def test_status_enum_documents_new_state(self):
        self.assertIn("audit_unavailable", _read("expert_escalation.py"))


class TestRuledOutNotBugs(unittest.TestCase):
    """诚实剔除: 验证后判定不是 bug 的项, 钉死结论防未来重复调查。"""

    def test_expert_quota_timestamps_are_internally_consistent(self):
        """UTC 日期不是 bug —— 写与读都用 UTC, 内部一致。"""
        src = _read("expert_escalation.py")
        # 所有 timestamp_iso 写入点都用 UTC
        self.assertNotIn('timestamp_iso": datetime.now().strftime', src)
        self.assertIn('datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")', src)
        # 读取侧也按 UTC 日期前缀比对
        i = src.index("def check_daily_quota")
        j = src.index("def write_audit_record")
        self.assertIn("timezone.utc", src[i:j])

    def test_dangerous_tokens_are_specific_multichar(self):
        """子串匹配对安全守卫是保守方向; token 必须够具体不误伤日常散文。"""
        for tok in ee._DANGEROUS_TOKENS:
            self.assertGreaterEqual(len(tok.strip()), 4, f"token 过短易误伤: {tok!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
