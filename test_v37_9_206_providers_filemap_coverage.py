"""test_v37_9_206_providers_filemap_coverage.py — V37.9.206 provider 插件部署覆盖守卫

血案背景 ("长期漏部署" bug 类的又一实例):
  V37.9.201/204 接入 deepseek/deepseek_full 两个 provider 插件, 但 auto_deploy.sh FILE_MAP
  漏登记 → auto_deploy 从不把它们 rsync 到运行时 ~/providers.d/ → adapter/proxy registry
  只有 8 个 provider → 用户设 `FALLBACK_PROVIDER=deepseek_full` 静默失效 (chain 停在 doubao,
  `explicit_fb in PROVIDERS` 为 False). Provider 插件不是 jobs_registry job, 绕过了原有的
  check_filemap_completeness job 循环, 所以 preflight/CI 从未抓到.

修复: check_registry.check_filemap_completeness 加 providers.d/*.py 覆盖扫描 (下划线前缀
  = 模板豁免, 与 providers.py 自动发现规则一致). 现在漏登记任何真 provider 插件 → 硬 error,
  preflight check 15 + full_regression 会抓到.

守卫维度:
  - 真仓库 providers.d/ 全部真插件都在 FILE_MAP (forward)
  - _example / 下划线前缀模板不被 flag (与 providers.py 自动发现豁免一致)
  - 反向验证: 从 FILE_MAP 移除一个真插件 → 被 flag (证扫描真做活非 tautology)
  - 源码守卫 marker
"""
import os
import shutil
import tempfile
import unittest

REPO_DIR = os.path.dirname(os.path.abspath(__file__))


class TestV37_9_206_ProvidersFileMapCoverage(unittest.TestCase):
    def test_real_repo_all_provider_plugins_in_filemap(self):
        """forward: 真仓库所有真 provider 插件都在 FILE_MAP, 无 providers.d error."""
        import check_registry
        errors, _ = check_registry.check_filemap_completeness(
            os.path.join(REPO_DIR, "jobs_registry.yaml")
        )
        prov_errs = [e for e in errors if "providers.d" in e]
        self.assertEqual(
            prov_errs, [],
            f"真仓库所有 provider 插件应都在 FILE_MAP (V37.9.201/204 deepseek 漏部署血案): {prov_errs}",
        )

    def test_underscore_template_not_flagged(self):
        """_example_provider.py (下划线前缀) 不被 flag — 与 providers.py 自动发现豁免一致."""
        import check_registry
        errors, _ = check_registry.check_filemap_completeness(
            os.path.join(REPO_DIR, "jobs_registry.yaml")
        )
        self.assertFalse(
            any("_example" in e for e in errors),
            "下划线前缀模板不应被要求进 FILE_MAP",
        )

    def test_deepseek_plugins_covered(self):
        """回归守卫: 两个 deepseek 插件都在 FILE_MAP (本血案的直接对象)."""
        deploy = open(os.path.join(REPO_DIR, "auto_deploy.sh"), encoding="utf-8").read()
        self.assertIn("providers.d/deepseek_provider.py|", deploy)
        self.assertIn("providers.d/deepseek_full_provider.py|", deploy)

    def test_reverse_validation_missing_plugin_flagged(self):
        """反向验证: FILE_MAP 移除一个真插件 → 被 flag (证扫描非 tautology)."""
        import check_registry
        with tempfile.TemporaryDirectory() as td:
            shutil.copytree(os.path.join(REPO_DIR, "providers.d"),
                            os.path.join(td, "providers.d"))
            shutil.copy(os.path.join(REPO_DIR, "jobs_registry.yaml"), td)
            shutil.copy(os.path.join(REPO_DIR, "path_consistency_scanner.py"), td)
            ad = open(os.path.join(REPO_DIR, "auto_deploy.sh"), encoding="utf-8").read()
            ad = ad.replace(
                "providers.d/deepseek_full_provider.py|$HOME/providers.d/deepseek_full_provider.py",
                "XXX_sabotaged_removed",
            )
            with open(os.path.join(td, "auto_deploy.sh"), "w", encoding="utf-8") as f:
                f.write(ad)
            errors, _ = check_registry.check_filemap_completeness(
                os.path.join(td, "jobs_registry.yaml")
            )
            self.assertTrue(
                any("deepseek_full_provider.py" in e and "providers.d" in e for e in errors),
                "移除真插件后应被 flag — 反证 providers.d 扫描真做活",
            )

    def test_source_marker_present(self):
        src = open(os.path.join(REPO_DIR, "check_registry.py"), encoding="utf-8").read()
        self.assertIn("V37.9.206", src, "check_registry 缺 V37.9.206 覆盖守卫 marker")
        self.assertIn("providers.d", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
