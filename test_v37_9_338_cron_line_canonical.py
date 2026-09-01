#!/usr/bin/env python3
"""V37.9.338 canonical crontab 行守卫。

血案（2026-08-31 → 09-01 生产实录，两天连报）：
V37.9.334 把 `check_upgrade` 登记进 jobs_registry（46→47 jobs），交接给用户的
crontab 行是**手写**的：

    10 9 * * 1 /bin/bash $HOME/check_upgrade.sh >> $HOME/check_upgrade.log 2>&1

它缺 `bash -lc`——脚本每周一照跑（09:10 首跑确实成功），但跑在 **cron 空环境**里
读不到 `~/.bash_profile`，正是 V28.1 立 INV-CRON-003 要防的事。后果：
  - 08-30 07:00 治理 760/760 rc=0（当时该行还没加）
  - 08-31 / 09-01 07:00 治理 759/760 rc=1，连报两天
  - 而 `preflight --full` 的 crontab↔registry 对账**通过**（它查存在性+间隔）
    → 两者差集恰好就是 `bash -lc` 这条断言，所以只有治理抓到

根因不是那一行写错，是**没有出口**：canonical 行的生成器
（`convergence._format_cron_line`，V37.9.23 起就在）只被 machine_sync 内部消费，
每次新登记 system job 都得手写。V37.9.334 的守卫断言了 registry `interval` 是
5 字段 cron，却从未断言交接命令的形态。

本守卫把**治理 07:00 才跑的判据前移到 dev CI**：用从 governance_ontology.yaml
抽出的 INV-CRON-003 真代码，跑在生成器产出的全部 canonical 行上。未来任何新登记
的 job 若会产出不合规的行，dev 就红，不必等次日 Mac Mini。
"""
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
GOV_YAML = os.path.join(ROOT, "ontology", "governance_ontology.yaml")
CONVERGENCE = os.path.join(ROOT, "ontology", "convergence.py")

# 2026-08-30 真实进入生产 crontab 的那一行（血案回归 fixture，逐字保留）
BLOOD_LINE = ("10 9 * * 1 /bin/bash $HOME/check_upgrade.sh "
              ">> $HOME/check_upgrade.log 2>&1")

try:
    import yaml
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False


def _inv_cron003_code():
    with open(GOV_YAML, encoding="utf-8") as f:
        d = yaml.safe_load(f)
    for inv in d.get("invariants", []):
        if inv["id"] != "INV-CRON-003":
            continue
        for c in inv.get("checks", []) or []:
            if c.get("requires_full") and "bash -lc" in str(c.get("code", "")):
                return c["code"]
    raise AssertionError("未在 governance_ontology.yaml 找到 INV-CRON-003 的 bash -lc check")


def _run_predicate(crontab_text):
    """用治理的真判据评估给定 crontab 文本。返回 (passed, message)。"""
    code = _inv_cron003_code()
    subbed = re.sub(
        r"crontab\s*=\s*subprocess\.check_output\([^\n]*\)",
        "crontab = __INJECTED__",
        code,
    )
    assert subbed != code, "防空转：未能替换 crontab 取数行，判据可能已改写"
    cwd = os.getcwd()
    try:
        os.chdir(ROOT)
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        ns = {"__INJECTED__": crontab_text}
        try:
            exec(compile(subbed, "<inv-cron-003>", "exec"), ns)
        except AssertionError as e:
            return False, str(e)
        return True, ""
    finally:
        os.chdir(cwd)


def _canonical_lines():
    p = subprocess.run([sys.executable, CONVERGENCE, "--cron-lines"],
                       capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert p.returncode == 0, f"--cron-lines 失败 rc={p.returncode}: {p.stderr[-300:]}"
    return [l for l in p.stdout.splitlines() if l.strip()]


@unittest.skipUnless(HAVE_YAML, "PyYAML 未安装")
class TestGeneratorSatisfiesGovernance(unittest.TestCase):
    """核心：生成器的产出必须满足治理的真判据（把 07:00 的检查前移到 dev）。"""

    def test_all_canonical_lines_pass_real_predicate(self):
        lines = _canonical_lines()
        self.assertGreaterEqual(len(lines), 30,
                                f"防空转：canonical 行应 ≥30，实际 {len(lines)}")
        ok, msg = _run_predicate("\n".join(lines))
        self.assertTrue(ok, f"生成器产出不满足 INV-CRON-003: {msg}")

    def test_blood_line_fails_real_predicate(self):
        """反向证据：真实血案行必须被同一判据抓住（证明上一条不是空转）。"""
        lines = [l for l in _canonical_lines() if "check_upgrade.sh" not in l]
        ok, msg = _run_predicate("\n".join(lines + [BLOOD_LINE]))
        self.assertFalse(ok, "血案行竟通过了 INV-CRON-003——判据或 fixture 已失效")
        self.assertIn("check_upgrade", msg)

    def test_every_canonical_line_has_bash_lc(self):
        offenders = [l for l in _canonical_lines() if "bash -lc" not in l]
        self.assertEqual([], offenders, f"生成器产出缺 bash -lc: {offenders}")


class TestCronLineCli(unittest.TestCase):
    """CLI 出口必须存在——它退役的是「手写 crontab 行」这个动作本身。"""

    def _run(self, *args):
        return subprocess.run([sys.executable, CONVERGENCE, *args],
                              capture_output=True, text=True, cwd=ROOT, timeout=60)

    def test_single_job_line(self):
        p = self._run("--cron-line", "check_upgrade")
        self.assertEqual(0, p.returncode, p.stderr[-300:])
        line = p.stdout.strip()
        self.assertIn("bash -lc", line)
        self.assertIn("check_upgrade.sh", line)
        self.assertTrue(line.startswith("10 9 * * 1 "), line)

    def test_unknown_job_is_actionable_error(self):
        p = self._run("--cron-line", "definitely_not_a_job")
        self.assertEqual(2, p.returncode)
        self.assertIn("known:", p.stderr, "未知 id 须列出可选 job，便于自纠")

    def test_blood_line_is_not_what_cli_emits(self):
        """CLI 的输出必须与 2026-08-30 手写的那行不同。"""
        p = self._run("--cron-line", "check_upgrade")
        self.assertNotEqual(BLOOD_LINE, p.stdout.strip())
        self.assertNotIn("/bin/bash $HOME", p.stdout)


class TestSourceGuards(unittest.TestCase):
    def setUp(self):
        with open(CONVERGENCE, encoding="utf-8") as f:
            self.src = f.read()

    def test_cli_flags_registered(self):
        for flag in ("--cron-line", "--cron-lines"):
            self.assertIn(flag, self.src, f"CLI 缺 {flag}")

    def test_cli_reuses_single_formatter(self):
        """MR-8：CLI 必须复用 _format_cron_line，不得自己拼字符串。"""
        i = self.src.index("def _cli_cron_lines")
        body = self.src[i:i + 2200]
        self.assertIn("_format_cron_line", body)
        self.assertNotIn("bash -lc '", body,
                         "CLI 不得内联 cron 行模板——那会造第二个真理源")

    def test_marker(self):
        self.assertIn("V37.9.338", self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
