"""test_v37_9_124_inv_runtime_narrowing.py — V37.9.124 日落法 #29 守卫

INV runtime check 收窄: INV-OBSERVER-001 的 runtime python_assert 从跑全
test_daily_observer (166 测试) 收窄到契约核心类 (TestV37_9_87/88/92/93 =
single-call/registry/status/sampling), 镜像 INV-DREAM-CROSS-DOMAIN 跑特定类.

缩 dev-production 接缝 (V37.9.121-hotfix2 血案: 全套件里任一 env-dependent test
让 INV 在 Mac Mini 误报 — test_missing_last_run 泄漏真实 last_run, _MAC_MINI_JOBS_DIR
fallback 读真实 ~/.openclaw/jobs).

#29 收窄约定 (给未来 INV 立约定):
  当 governance INV runtime check 跑的测试套件【含 env-dependent 测试】(读 repo 外真实
  系统状态: _MAC_MINI_JOBS_DIR / 真实路径 / crontab / services), INV 应跑【契约核心类】
  而非全套件, 让 Mac Mini governance audit cron 不被 env-dependent test 误报.
  契约行为由 INV inline check 直接验证 (env-independent); full_regression (dev) 仍跑
  全套件 catch 一切. 纯逻辑套件 (env-independent) 跑全套件 OK, 不强制收窄 (避免 churn).

为何只收窄 INV-OBSERVER-001:
  #29 调查确认唯一【已证明】env-dependent 的套件是 test_daily_observer (V37.9.121-hotfix2).
  其他全套件 INV (router/scanner/health 等) 套件 env-independent (纯逻辑/已隔离/relative-to-HOME
  一致) — 原则 #18 补证据非补功能, 不收窄 (churn).

测试:
  - INV-OBSERVER-001 runtime 用契约类动态发现 (TestV37_9_87/88/92/93) 非全文件
  - for-loop 非 comprehension (V37.3 exec 作用域陷阱)
  - 契约核心类可发现 (≥12) + 收窄后真跑通 (behavioral)
  - env-dependent general 类 (TestResolveLastRunPath/TestScanJobStatuses) 被排除 + 反向有 env 信号
"""
import os
import re
import subprocess
import sys
import unittest

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
GOV_YAML = os.path.join(REPO_DIR, "ontology", "governance_ontology.yaml")
OBS_TEST = os.path.join(REPO_DIR, "test_daily_observer.py")

# 契约核心类正则 (与 INV-OBSERVER-001 runtime check 同款单一源)
_CONTRACT_RE = r"^class (TestV37_9_(?:87|88|92|93)_\w+)"


def _inv_observer_block():
    """从 governance_ontology.yaml 提取 INV-OBSERVER-001 块 (到下一个 INV)."""
    with open(GOV_YAML, encoding="utf-8") as f:
        content = f.read()
    oi = content.find("id: INV-OBSERVER-001")
    assert oi != -1, "INV-OBSERVER-001 未找到"
    nxt = content.find("\n  - id: INV-", oi + 1)
    return content[oi:nxt if nxt > 0 else len(content)]


def _obs_test_src():
    with open(OBS_TEST, encoding="utf-8") as f:
        return f.read()


class TestV37_9_124_RuntimeNarrowing(unittest.TestCase):
    def setUp(self):
        self.block = _inv_observer_block()

    def test_runs_contract_classes_not_full_file(self):
        # 收窄: 用 -m unittest + 契约类动态发现, 不跑全文件 [sys.executable, _test]
        self.assertIn("-m", self.block)
        self.assertIn("unittest", self.block)
        self.assertIn("TestV37_9_(?:87|88|92|93)", self.block,
                      "应动态发现契约核心类 (TestV37_9_87/88/92/93)")
        # 反 full-file 反模式 (V37.9.121-hotfix2 前的脆弱写法)
        self.assertNotIn("[sys.executable, _test]", self.block,
                         "不应跑全 test_daily_observer.py 文件 (#29 收窄前反模式)")
        self.assertIn("V37.9.124", self.block)
        self.assertIn("V37.9.121-hotfix2", self.block, "应引用血案出处")

    def test_uses_for_loop_not_comprehension(self):
        # V37.3 exec 作用域陷阱: 用 for-loop 不用 comprehension
        self.assertIn("for _c in _contract:", self.block)
        flat = self.block.replace("\n", " ")
        self.assertNotIn("for _c in _contract]", flat,
                         "禁 list comprehension (V37.3 exec 作用域陷阱)")

    def test_contract_classes_discoverable_and_pass(self):
        # 契约核心类可发现 (≥12) + 收窄后的 -m unittest 调用真跑通 (behavioral)
        src = _obs_test_src()
        contract = re.findall(_CONTRACT_RE, src, re.MULTILINE)
        self.assertGreaterEqual(
            len(contract), 12,
            f"契约核心类 < 12 (找到 {len(contract)}) — daily_observer 契约测试类异常缺失",
        )
        targets = []
        for c in contract:
            targets.append("test_daily_observer." + c)
        r = subprocess.run(
            [sys.executable, "-m", "unittest"] + targets,
            capture_output=True, text=True, timeout=120, cwd=REPO_DIR,
        )
        self.assertEqual(r.returncode, 0,
                         f"契约核心类收窄运行失败: {r.stderr[-800:]}")

    def test_env_dependent_general_classes_excluded(self):
        # env-dependent general 类不在契约版本号集 (被收窄排除)
        src = _obs_test_src()
        contract = re.findall(_CONTRACT_RE, src, re.MULTILINE)
        self.assertNotIn("TestResolveLastRunPath", contract)
        self.assertNotIn("TestScanJobStatuses", contract)

    def test_reverse_excluded_classes_have_env_signal(self):
        # 反向: 被排除的 general 类确实有 env 信号 (_MAC_MINI_JOBS_DIR) — 证排除有意义非随意
        src = _obs_test_src()
        for cls in ("TestResolveLastRunPath", "TestScanJobStatuses"):
            ci = src.find(f"class {cls}")
            self.assertNotEqual(ci, -1, f"{cls} 应存在")
            nxt = src.find("\nclass ", ci + 1)
            body = src[ci:nxt if nxt > 0 else len(src)]
            self.assertIn(
                "_MAC_MINI_JOBS_DIR", body,
                f"{cls}: 被排除的 general 类应有 env 信号 — 证 #29 排除有意义",
            )

    def test_contract_classes_dont_leak_real_mac_mini_dir(self):
        # 契约核心类 env-independent: 不在 setUp/test 把 obs._MAC_MINI_JOBS_DIR 赋成真实 ~ 路径
        # (那是 env-dependent 泄漏源 — V37.9.121-hotfix2 血案). 契约类应 mock/隔离或不触碰.
        src = _obs_test_src()
        contract = re.findall(_CONTRACT_RE, src, re.MULTILINE)
        leak_pat = re.compile(
            r'_MAC_MINI_JOBS_DIR\s*=\s*os\.path\.expanduser\(\s*["\']~'
        )
        for cls in contract:
            ci = src.find(f"class {cls}")
            nxt = src.find("\nclass ", ci + 1)
            body = src[ci:nxt if nxt > 0 else len(src)]
            self.assertIsNone(
                leak_pat.search(body),
                f"{cls}: 契约核心类不应把 _MAC_MINI_JOBS_DIR 赋成真实 ~ 路径 (env-dependent 泄漏)",
            )

    def test_marker_in_governance_yaml(self):
        self.assertIn("V37.9.124", self.block, "INV-OBSERVER-001 缺 V37.9.124 收窄 marker")
        self.assertIn("日落法 #29", self.block)


if __name__ == "__main__":
    unittest.main()
