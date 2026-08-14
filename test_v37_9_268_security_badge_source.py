#!/usr/bin/env python3
"""V37.9.268 守卫: gen_readme_badges 徽章源 vs full_regression 实时 security 解耦。

根因 (V37.9.267 厘清, V37.9.243/245 长期"gen_readme_badges 偶发失败观察项"):
  full_regression.sh 的安全评分块曾 `status_update.py --set quality.security_score <实时值>`,
  而 gen_readme_badges.py:104 读 quality.security_score 作 doc header 徽章源。
  security_score 传输安全的 Git 协议子项跑 `git remote -v` — dev 沙箱 remote 是 http-proxy
  (0 分) vs 生产 SSH (5 分) ⇒ dev security 95 vs 生产 98 = 纯 dev 环境 artifact。
  每次 dev full_regression 用 95 覆盖徽章源 → 末尾 doc-drift 检测 doc header(98) 假失败。

修复 (V37.9.268): full_regression 不再覆盖徽章源 quality.security_score, 只记
  health.security_score 作环境敏感诊断值。quality.security_score 冻结为稳定发布态徽章源
  (生产真变时手动 --set)。dev/生产 full_regression 都不再因 security 环境差异报 doc-drift。

守卫: 源码级确认 full_regression 不再写 quality.security_score(命令, 非注释提及) +
  仍记 health.security_score + gen_readme_badges 读 quality (徽章源契约) + marker。
"""
import os
import re
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
FULL_REG = os.path.join(REPO, "full_regression.sh")
GEN_BADGES = os.path.join(REPO, "gen_readme_badges.py")
KB_STATUS_REFRESH = os.path.join(REPO, "kb_status_refresh.sh")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestSecurityBadgeSourceDecoupling(unittest.TestCase):
    """V37.9.268: 徽章源 quality.security_score 不被 full_regression 用环境实时值覆盖。"""

    def setUp(self):
        self.fr = _read(FULL_REG)

    def test_full_regression_no_quality_security_score_command(self):
        """防回退: full_regression 不含 `status_update.py --set quality.security_score` 命令。

        注意用命令模式匹配 (status_update.py --set quality.security_score), 不是裸
        'quality.security_score' — 后者会匹配到修复注释里的说明文字 (合法保留)。
        """
        cmd = re.search(r"status_update\.py\s+--set\s+quality\.security_score\b", self.fr)
        self.assertIsNone(
            cmd,
            "full_regression 不应再用 `--set quality.security_score` 命令覆盖徽章源 "
            "(V37.9.268 根因: dev 环境敏感值污染 doc header 徽章致假 doc-drift)",
        )

    def test_full_regression_no_quality_security_score_time_command(self):
        """连带: quality.security_score_time 也不再由 full_regression 设 (随 quality 一起退役)。"""
        cmd = re.search(r"status_update\.py\s+--set\s+quality\.security_score_time\b", self.fr)
        self.assertIsNone(cmd, "full_regression 不应再设 quality.security_score_time")

    def test_full_regression_still_records_health_security(self):
        """保留诊断: full_regression 仍记 health.security_score (环境敏感实时值, 不驱动徽章)。"""
        cmd = re.search(r"status_update\.py\s+--set\s+health\.security_score\b", self.fr)
        self.assertIsNotNone(
            cmd,
            "full_regression 应保留 health.security_score 记录作诊断 (环境敏感值可观测)",
        )

    def test_gen_readme_badges_reads_quality_security_score(self):
        """徽章源契约不变: gen_readme_badges 仍从 quality.security_score 读徽章值。

        这是本修复依赖的前提 — 徽章源是 quality.security_score, 我们让它稳定(不被覆盖),
        而非改 gen_readme_badges 读别处。若上游改了读取源, 本守卫先 fail 提醒重审修复。
        """
        gb = _read(GEN_BADGES)
        self.assertRegex(
            gb,
            r'quality\.get\(\s*["\']security_score["\']\s*\)',
            "gen_readme_badges 应从 quality.security_score 读徽章 (徽章源契约)",
        )

    def test_v37_9_268_marker_and_rationale(self):
        """marker + 根因注释在位 (防注释被误删丢失修复 rationale)。"""
        self.assertIn("V37.9.268", self.fr, "full_regression 应含 V37.9.268 marker")
        # 根因关键词: Git 协议子项 / 徽章源 / 发布态
        self.assertIn("徽章源", self.fr, "应保留徽章源根因注释")
        self.assertIn("发布态", self.fr, "应保留 quality 作稳定发布态徽章源的说明")


class TestV379303AllWritersConverged(unittest.TestCase):
    """V37.9.303 (SLO 读侧审计 F6): 冻结徽章源的写者全量收敛 (原则 #31)。

    V37.9.268 只 pin 了 full_regression 一个写者; kb_status_refresh.sh 4b 段每小时
    cron 仍在 `--set quality.security_score <环境实时值>` = 漏网第二写者, 冻结不变式
    被每小时静默打破 (当前实时分恰==98 徽章值 → 零可见症状, 这正是它存活的原因)。
    本类把守卫扩到全仓: 除手动操作外, 任何脚本不得写 quality.security_score。
    """

    def setUp(self):
        self.ksr = _read(KB_STATUS_REFRESH)

    def test_kb_status_refresh_no_quality_security_score_command(self):
        """kb_status_refresh 不得再用 `--set quality.security_score` 覆盖冻结徽章源。"""
        cmd = re.search(r"--set\s+quality\.security_score\b", self.ksr)
        self.assertIsNone(
            cmd,
            "kb_status_refresh.sh 不应写 quality.security_score (V37.9.268 冻结徽章源仅手动 --set; "
            "本文件曾是漏网第二写者, 每小时用环境实时值覆盖)",
        )

    def test_kb_status_refresh_records_health_security(self):
        """诊断值归 health.security_score (与 full_regression V37.9.268 同向)。"""
        cmd = re.search(r"--set\s+health\.security_score\b", self.ksr)
        self.assertIsNotNone(cmd, "kb_status_refresh.sh 应把实时 security 记进 health.security_score")

    def test_no_other_script_writes_quality_security_score(self):
        """全仓扫描: 运行时 .sh/.py 中不存在其他 `--set quality.security_score` 写者
        (防未来第三写者重演原则 #31 漏同步; 文档/注释/测试提及不算)。"""
        offenders = []
        for root, dirs, files in os.walk(REPO):
            dirs[:] = [d for d in dirs if d not in (".git", "docs", "__pycache__", "node_modules")]
            for fn in files:
                if not (fn.endswith(".sh") or fn.endswith(".py")) or fn.startswith("test_"):
                    continue
                # status_update.py 自身豁免: 其 CLI 帮助文档以 docstring 示范
                # `--set quality.security_score 92` = 被 V37.9.268 认可的手动操作入口,
                # 不是自动写者 (它是被调用的 helper, 不主动发起写)。
                if fn == "status_update.py":
                    continue
                path = os.path.join(root, fn)
                try:
                    content = _read(path)
                except (OSError, UnicodeDecodeError):
                    continue
                for m in re.finditer(r"--set\s+quality\.security_score\b", content):
                    line_start = content.rfind("\n", 0, m.start()) + 1
                    line = content[line_start:content.find("\n", m.start())]
                    if line.lstrip().startswith("#"):
                        continue  # 注释提及 (如修复说明) 合法
                    offenders.append(os.path.relpath(path, REPO))
        self.assertEqual(
            offenders, [],
            f"发现新的 quality.security_score 脚本写者 (冻结徽章源仅手动 --set): {offenders}")

    def test_v37_9_303_marker(self):
        """kb_status_refresh 带 V37.9.303 修复注释 (根因可追溯)。"""
        self.assertIn("V37.9.303", self.ksr)


class TestBehaviorQualityStable(unittest.TestCase):
    """行为级: 在临时 status.json 上确认 --set health 不动 quality (解耦真实成立)。"""

    def test_set_health_does_not_touch_quality(self):
        """status_update --set health.security_score 只改 health, quality.security_score 不变。

        这是修复的核心不变式: full_regression 记 health 时, 徽章源 quality 保持不动。
        用真实 status_update.py 在隔离 HOME 下验证 (不污染真实 status.json — MR-9)。
        """
        import json
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            # 构造隔离 status.json: quality=98 (发布态), health=旧值
            kb = os.path.join(td, ".kb")
            os.makedirs(kb, exist_ok=True)
            status_path = os.path.join(kb, "status.json")
            with open(status_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"quality": {"security_score": 98}, "health": {"security_score": "98/100 (98.0%)"}},
                    f,
                )
            env = dict(os.environ, HOME=td, KB_BASE=kb)
            # 模拟 full_regression 用 dev 值 95 只更新 health
            r = subprocess.run(
                ["python3", os.path.join(REPO, "status_update.py"),
                 "--set", "health.security_score", "95/100 (95.0%)", "--by", "full_regression"],
                capture_output=True, text=True, env=env, cwd=td, timeout=20,
            )
            self.assertEqual(r.returncode, 0, f"status_update --set health 应成功: {r.stderr}")
            with open(status_path, encoding="utf-8") as f:
                after = json.load(f)
            # 核心断言: 徽章源 quality.security_score 未被 health 更新触动
            self.assertEqual(
                after["quality"]["security_score"], 98,
                "更新 health.security_score 不应改动徽章源 quality.security_score (解耦不变式)",
            )
            self.assertEqual(
                after["health"]["security_score"], "95/100 (95.0%)",
                "health.security_score 应记录 dev 诊断值 95",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
