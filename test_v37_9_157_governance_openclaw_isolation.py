#!/usr/bin/env python3
"""V37.9.157 守卫: governance runtime check 跑的 openclaw-invoking subprocess 必须 stub openclaw.

血案 (2026-06-15 用户 Mac Mini governance --full): INV-CRON-MONITOR-001 + INV-REVIEW-001 两个
💥 (执行出错). 真因 = 4.27 openclaw CLI 冷调用 (~10s/次) 劫持治理 runtime python_assert 的
subprocess (test_cron_monitor_fatal_handler.py 调真 openclaw 77s > 60s timeout; kb_review.sh
推送步走真 4.27 撑爆 30s). 且真 openclaw 会往用户 WhatsApp 发真 [SYSTEM_ALERT] (test-pollutes-
production). MR-23 audit-observes-never-mutates: 治理审计的 subprocess 绝不依赖/调真生产 CLI.

V37.9.110/113/116/145 同族隔离的第 N 次演出. 本守卫机器化该契约防回归.
"""
import os
import re
import subprocess
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent
GOV_YAML = REPO / "ontology" / "governance_ontology.yaml"
CRON_TEST = REPO / "test_cron_monitor_fatal_handler.py"
CONV_TEST = REPO / "test_convergence.py"  # V37.9.158-hotfix 守卫目标

# notify.sh / helper 解析 openclaw 的三档 fallback: ${OPENCLAW_BIN:-${OPENCLAW:-/opt/homebrew/bin/openclaw}}
# 任何跑 openclaw-invoking subprocess 的治理/测试隔离都必须同时覆盖 OPENCLAW_BIN + OPENCLAW.


class TestCronMonitorTestStubsOpenclaw(unittest.TestCase):
    """test_cron_monitor_fatal_handler.py 的 helper 必须 stub openclaw (不调真 4.27)."""

    @classmethod
    def setUpClass(cls):
        cls.src = CRON_TEST.read_text(encoding="utf-8")

    def test_v37_9_157_marker(self):
        self.assertIn("V37.9.157", self.src, "缺 V37.9.157 stub 隔离 marker")

    def test_stub_env_helper_defined(self):
        self.assertIn("def _stub_env", self.src, "缺 _stub_env 受控环境 helper")

    def test_stub_env_sets_both_openclaw_vars(self):
        # 必须同时设 OPENCLAW_BIN + OPENCLAW (覆盖三档 fallback)
        m = re.search(r"def _stub_env.*?(?=\n    def )", self.src, re.DOTALL)
        self.assertIsNotNone(m, "未定位 _stub_env 方法体")
        body = m.group(0)
        self.assertIn('"OPENCLAW_BIN"', body, "_stub_env 必须设 OPENCLAW_BIN")
        self.assertIn('"OPENCLAW":', body, "_stub_env 必须设 OPENCLAW")

    def test_stub_path_excludes_real_openclaw_dir(self):
        # 检查 path 赋值行本身 (注释里提及 /opt/homebrew/bin 解释 fallback 链是合法的)
        path_assign = [ln for ln in self.src.split("\n")
                       if "path = " in ln and "stub_bin" in ln]
        self.assertTrue(path_assign, "未找到 _stub_env 的 path 赋值行")
        self.assertNotIn("/opt/homebrew/bin", path_assign[0],
                         "_stub_env PATH 赋值不得含 /opt/homebrew/bin (真 4.27 openclaw 会被触达)")

    def test_run_helper_uses_stub_env(self):
        # _run_helper_in_subshell 必须用 env=self._stub_env(...) 而非继承 os.environ
        m = re.search(r"def _run_helper_in_subshell.*?return ", self.src, re.DOTALL)
        self.assertIn("env=self._stub_env", m.group(0),
                      "_run_helper_in_subshell 必须传 env=self._stub_env (不继承 Mac Mini 真环境)")

    def test_set_e_test_uses_stub_env(self):
        # test_helper_works_with_set_e 的 subprocess 也必须 stub (否则它单独调真 openclaw)
        m = re.search(r"def test_helper_works_with_set_e.*?(?=\n    def |\nclass )", self.src, re.DOTALL)
        self.assertIn("env=self._stub_env", m.group(0),
                      "test_helper_works_with_set_e 必须用 _stub_env")


class TestInvReviewCheckStubsOpenclaw(unittest.TestCase):
    """governance_ontology.yaml INV-REVIEW-001 runtime check 必须完整 stub openclaw."""

    @classmethod
    def setUpClass(cls):
        cls.yaml = GOV_YAML.read_text(encoding="utf-8")

    def _inv_review_check_block(self) -> str:
        # 定位 'V37.5.1 runtime: 真实 subprocess 执行 kb_review.sh' check 块
        idx = self.yaml.find("V37.5.1 runtime: 真实 subprocess 执行 kb_review.sh")
        self.assertGreater(idx, 0, "未找到 INV-REVIEW-001 kb_review.sh runtime check")
        return self.yaml[idx: idx + 5000]

    def test_v37_9_157_marker_in_check(self):
        self.assertIn("V37.9.157", self._inv_review_check_block(),
                      "INV-REVIEW-001 check 缺 V37.9.157 stub gap 修复 marker")

    def test_sets_openclaw_bin(self):
        self.assertIn('"OPENCLAW_BIN": stub', self._inv_review_check_block(),
                      "INV-REVIEW-001 check 必须设 OPENCLAW_BIN=stub (关三档 fallback gap)")

    def test_sets_notify_max_retries_one(self):
        self.assertIn('"NOTIFY_MAX_RETRIES": "1"', self._inv_review_check_block(),
                      "INV-REVIEW-001 check 必须设 NOTIFY_MAX_RETRIES=1 (防 retry sleep 累积)")

    def test_path_excludes_real_openclaw_dir(self):
        block = self._inv_review_check_block()
        # 该 check 的 PATH 行不得含 /opt/homebrew/bin
        path_lines = [ln for ln in block.split("\n") if '"PATH"' in ln]
        self.assertTrue(path_lines, "未找到 PATH 行")
        self.assertNotIn("/opt/homebrew/bin", path_lines[0],
                         "INV-REVIEW-001 check PATH 不得含 /opt/homebrew/bin")

    def test_copies_notify_sh_to_route_push_through_stub(self):
        # 真根因: kb_review.sh line 16 重加 /opt/homebrew/bin + push 用裸 openclaw → 真 4.27.
        # 修: 复制 notify.sh 让 push 走 notify() → "$OPENCLAW"=stub (用变量非裸命令).
        self.assertIn('shutil.copy(os.path.join(repo_dir, "notify.sh"), tmp)',
                      self._inv_review_check_block(),
                      "INV-REVIEW-001 check 必须复制 notify.sh (push 走 $OPENCLAW=stub 非裸 openclaw)")

    def test_stubs_rsync_helper(self):
        # kb_review.sh line 225 调 movespeed_rsync_helper.sh (V37.9.27 jitter 30-180s) → 必 stub
        block = self._inv_review_check_block()
        self.assertIn("movespeed_rsync_helper.sh", block,
                      "INV-REVIEW-001 check 必须 stub movespeed_rsync_helper.sh (防 jitter 30-180s 超 timeout)")


class TestNoUnstubbedOpenclawScriptSubprocess(unittest.TestCase):
    """前瞻守卫: 任何治理 check 用 subprocess 跑 kb_review.sh 都必须设 OPENCLAW stub.

    防止未来新增治理 runtime check 跑推送脚本时漏 stub → 又被 4.27 冷调用劫持 💥 + 真发消息.
    """

    def test_kb_review_subprocess_blocks_stub_openclaw(self):
        yaml = GOV_YAML.read_text(encoding="utf-8")
        # 找所有跑 kb_review.sh 的 subprocess.run 站点, 每个附近窗口必须有 OPENCLAW stub
        violations = []
        for m in re.finditer(r'subprocess\.run\(', yaml):
            window = yaml[m.start(): m.start() + 1500]
            if "kb_review.sh" in window:
                if '"OPENCLAW"' not in window:
                    line_no = yaml[: m.start()].count("\n") + 1
                    violations.append(line_no)
        self.assertEqual(violations, [],
                         f"治理 check 跑 kb_review.sh 但未 stub OPENCLAW (4.27 劫持风险), 行: {violations}")


class TestBehavioralCronMonitorFast(unittest.TestCase):
    """行为级: test_cron_monitor_fatal_handler.py 必须秒过 (证明不依赖真 openclaw 冷调用).

    dev 无真 openclaw 本就快; Mac Mini 有真 4.27, 若 stub 失效 → 77s. 此守卫在 Mac Mini 上
    真正捕获 'helper 又调真 openclaw' 回归 (governance check timeout=60).
    """

    def test_completes_well_under_governance_timeout(self):
        t0 = time.time()
        r = subprocess.run(
            ["python3", str(CRON_TEST)],
            cwd=str(REPO), capture_output=True, text=True, timeout=55,
        )
        elapsed = time.time() - t0
        self.assertEqual(r.returncode, 0, f"test_cron_monitor 未通过:\n{r.stderr[-800:]}")
        # governance check timeout=60; 必须远低于 (stub 生效则 < 数秒)
        self.assertLess(elapsed, 30,
                        f"test_cron_monitor 耗时 {elapsed:.1f}s — stub 失效, helper 在调真 openclaw 冷调用")


class TestConvergenceTestNoCrontabMutation(unittest.TestCase):
    """V37.9.158-hotfix 守卫: test_convergence.py 任何 dry_run=False real-apply 测试必须隔离
    (HOME→tempdir 或 mock subprocess), 绝不触碰真 ~/crontab_safe.sh / ~/kb_embed.py / launchctl.

    血案 (2026-06-15): test_explicit_dry_run_overrides_env 传 dry_run=False 走 real-apply,
    os.path.expanduser('~/crontab_safe.sh') 在 Mac Mini 解析到**真** helper → 真 add → 重加
    INV-CRON-004 auto_deploy 双行. governance INV-CONVERGENCE-CRON-001 runtime check 跑
    test_convergence.py 子进程时在 Mac Mini 真 mutate 生产 crontab — V37.9.158 默认 dry-run
    **未覆盖此 gap** (该测试故意显式 dry_run=False 绕过默认). MR-9/MR-23 测试污染生产同族
    (auto_deploy 双行 V37.9.106/111/116/120/158 反复 5 次复发的真根因之一).
    """

    # 任一信号即视为隔离 (保证 real-apply 路径绝不触达真 subprocess + 真路径):
    _ISOLATION_SIGNALS = (
        'os.environ["HOME"]',                              # HOME 重定向 (real helper 路径→tempdir)
        "cv.subprocess.run =",                              # 直接 mock subprocess
        "_set_subprocess_mock",                             # TestApplyMachineSyncReal helper mock
        'mock.patch("subprocess.run"',                      # 上下文 mock subprocess (services)
        'mock.patch("os.path.exists", return_value=False)', # helper-missing 短路 → not-found 分支, 永不到 subprocess
    )

    @classmethod
    def setUpClass(cls):
        cls.src = CONV_TEST.read_text(encoding="utf-8")

    def _override_body(self) -> str:
        m = re.search(
            r"def test_explicit_dry_run_overrides_env.*?(?=\n    def |\nclass )",
            self.src, re.DOTALL)
        self.assertIsNotNone(m, "未定位 test_explicit_dry_run_overrides_env 方法体")
        return m.group(0)

    def test_v37_9_158_isolation_marker(self):
        self.assertIn("V37.9.158", self._override_body(),
                      "test_explicit_dry_run_overrides_env 缺 V37.9.158 隔离 marker")

    def test_override_redirects_home(self):
        # 检查真实重定向赋值 (os.environ["HOME"] = td), 非 finally 的 restore 行.
        self.assertIn('os.environ["HOME"] = td', self._override_body(),
                      "test_explicit_dry_run_overrides_env 必须重定向 HOME→tempdir (防解析真 ~/crontab_safe.sh)")

    def test_override_mocks_subprocess(self):
        body = self._override_body()
        self.assertIn("cv.subprocess.run = _never_run", body,
                      "必须 mock cv.subprocess.run (belt-and-suspenders, 任何真执行立即 AssertionError)")
        self.assertIn("def _never_run", body, "缺 _never_run 防御函数")

    def test_override_asserts_no_real_apply(self):
        self.assertIn("self.assertEqual(applied, ()", self._override_body(),
                      "必须断言 applied 为空 (证明隔离后真模式无 helper → 绝不真改 crontab)")

    def test_forward_scan_real_apply_tests_isolated(self):
        """前瞻: test_convergence.py 任何含真实 dry_run=False kwarg 调用的测试方法必须隔离
        (HOME 重定向 或 subprocess mock). 防未来新增 real-apply 测试漏隔离 → 在 Mac Mini
        governance subprocess 跑 test_convergence.py 时真 mutate 生产 state.
        """
        # 切方法块 (split 前缀 '\n    def '); 每块首 token 是方法名.
        methods = re.split(r"\n    def ", self.src)
        violations = []
        for blk in methods:
            # 逐行剥行尾注释 (避免 setUp 注释里的 'dry_run=False' 误触)
            code = "\n".join(ln.split("#", 1)[0] for ln in blk.split("\n"))
            # 仅真实 kwarg: 负向 lookbehind 排除 apply_dry_run=False 字段引用
            if not re.search(r"(?<![A-Za-z_])dry_run=False", code):
                continue
            name_m = re.match(r"(\w+)", blk)
            name = name_m.group(1) if name_m else "?"
            if not any(sig in blk for sig in self._ISOLATION_SIGNALS):
                violations.append(name)
        self.assertEqual(violations, [],
            "test_convergence.py 这些 dry_run=False real-apply 测试未隔离 (HOME/subprocess mock) "
            "— 在 Mac Mini governance subprocess 跑时会真 mutate 生产 state: " + repr(violations))


class TestReverseValidation(unittest.TestCase):
    def test_sabotage_documented(self):
        # 反向验证: 删 _stub_env 的 OPENCLAW_BIN → test_stub_env_sets_both_openclaw_vars 立即 fail;
        # INV-REVIEW-001 check 删 OPENCLAW_BIN → test_sets_openclaw_bin 立即 fail;
        # test_explicit_dry_run_overrides_env 删 HOME 重定向 → test_override_redirects_home 立即 fail.
        # 此处确认守卫锚点字面量存在 (防整体被删).
        self.assertIn("OPENCLAW_BIN", CRON_TEST.read_text(encoding="utf-8"))
        self.assertIn("OPENCLAW_BIN", GOV_YAML.read_text(encoding="utf-8"))
        self.assertIn("V37.9.158", CONV_TEST.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
