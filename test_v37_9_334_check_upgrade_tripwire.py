#!/usr/bin/env python3
"""V37.9.334 — check_upgrade.sh 升级 tripwire 审计守卫（2026-08-30 对抗审计）

血案背景（"验证者自身无人验证"家族——gameday V37.9.331 / kb_integrity V37.9.324 /
job_watchdog 死告警 V37.9.328 同谱系）:
check_upgrade.sh 是升级决策的安全网（V37.9.22 立），每份评估文档都声称
"每周一 cron 自动监控 + 任一 tripwire 触发推送告警"，但对抗审计发现:

  CU-F1 (HIGH) 零登记零接线 — 脚本不在 jobs_registry.yaml（违反"新增任务必须先登记"
        宪法）/ 不在 auto_deploy FILE_MAP（运行时副本无人同步）/ 不在 job_watchdog
        任何名单（它死了没人会发现）/ 自身零 notify 调用（"触发推送告警"没有任何
        机制支撑 = tripwire 响了没有线连到铃上，V37.9.328 死告警家族）。
  CU-F2 (MED) 版本差距字典序比较 — `'2026.10.1' < '2026.4.27'`（'1'<'4'）→ 2026.10.x
        起（本审计 5 周后）上游 stable 系统性漏计；'2026.' 前缀过滤 → 2027.1.x 起同款。
  CU-F3 (MED) 网络失败读作全绿 — curl 失败/响应异常时 tripwire 2 报 "0/50 ✅"、
        3/4 报 "未检出 ✅"（跑不动 ≠ 没新版，V37.9.322 F3 家族）。dev 实证: GitHub API
        在 dev egress 下一直不可达，历次评估的 dev 运行都显示过假 ✅。
  CU-F4 (LOW-MED) EOL 关键词硬编码 'v2026.3' — 部署 2026.3.13 时代遗留；部署已 4.27
        十一周，对 v2026.4 的 EOL 抓不到、对无关的 v2026.3 反而误报。

同批 rider: proxy_filters/job_watchdog 的 "Qwen context 临界/预警" 告警品牌陈旧
（primary V37.9.222 起 doubao_21）→ "模型 context"（whiplash-resistant，两处一物一形）。

测试分层:
  A. TestVersionGapNumericCompare — 从源码提取 tripwire-2 python 真跑（防守卫-实现漂移）
     + 反向证据（旧字典序在同一 fixture 上确实漏计 = 修复 load-bearing 非空转）
  B. TestScriptBehaviorHermetic — fakebin curl/openclaw/npm + 隔离 HOME 全脚本行为级
     （fake openclaw 同时防 Mac Mini 跑测试时真调 `plugins install` 副作用）
  C. TestWiringGuards — registry/FILE_MAP/watchdog 跨文件契约 + 源码守卫（剥注释，
     V37.9.178 "守卫被自己的注释咬"家族）
"""

import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta

REPO = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(REPO, "check_upgrade.sh")


def _read(name):
    with open(os.path.join(REPO, name), encoding="utf-8") as f:
        return f.read()


def _executable_lines(text):
    """剥注释行（V37.9.178 家族: 修复注释里逐字写着被退役的形态，不剥会被自己咬）。"""
    out = []
    for ln in text.splitlines():
        if ln.lstrip().startswith("#"):
            continue
        out.append(ln)
    return "\n".join(out)


def _extract_tripwire2_python(src):
    """从 check_upgrade.sh 切出 tripwire-2 的 python 块（真源码，防守卫-实现漂移）。"""
    start = src.index("Tripwire 2:")
    end = src.index("Tripwire 3/4")
    block = src[start:end]
    m = re.search(r'python3 -c "(.*?)"', block, re.DOTALL)
    assert m, "tripwire-2 python 块未找到"
    code = m.group(1)
    assert "vkey" in code, "提取到的块不含 vkey = 切错了块（防空转）"
    return code


def _run_gap_python(code, stdin_text, current="2026.4.27"):
    env = os.environ.copy()
    env["CURRENT_V"] = current
    r = subprocess.run([sys.executable, "-c", code], input=stdin_text,
                       capture_output=True, text=True, env=env, timeout=30)
    return r.stdout.strip()


class TestVersionGapNumericCompare(unittest.TestCase):
    """A. CU-F2/CU-F3: tripwire-2 python 行为级（从真源码提取）。"""

    @classmethod
    def setUpClass(cls):
        cls.code = _extract_tripwire2_python(_read("check_upgrade.sh"))

    def test_blood_case_oct_2026_and_2027_counted(self):
        """血案回归: 2026.10.x / 2026.12.x / 2027.1.x 必须计入 after（旧字典序漏计）。"""
        vs = ["2026.4.27", "2026.9.1", "2026.10.1", "2026.12.3", "2027.1.1"]
        fake = '{"versions": {' + ",".join(f'"{v}": {{}}' for v in vs) + "}}"
        out = _run_gap_python(self.code, fake)
        self.assertEqual(out, "4", f"数值序应计 4 个 after，实际 {out!r}")
        # 反向证据（防空转）: 旧字典序 + '2026.' 前缀在同一 fixture 上只数出 1
        lex = len([v for v in vs if v.startswith("2026.") and v > "2026.4.27"])
        self.assertEqual(lex, 1, "前提自检: 旧算法在此 fixture 上确实漏计")
        self.assertNotEqual(out, str(lex), "数值序结果必须区别于旧字典序（证修复 load-bearing）")

    def test_patch_suffix_parses_and_counts(self):
        """'2026.7.1-2' 风格 patch 后缀必须可解析且计入（-N 是本项目 stable 惯例非 prerelease）。"""
        fake = '{"versions": {"2026.7.1-2": {}}}'
        self.assertEqual(_run_gap_python(self.code, fake), "1")

    def test_prerelease_excluded(self):
        fake = ('{"versions": {"2026.9.9-beta.1": {}, "2026.9.8-alpha.2": {}, '
                '"2026.9.7-rc.1": {}, "2026.9.6-dev.1": {}}}')
        self.assertEqual(_run_gap_python(self.code, fake), "0")

    def test_current_itself_not_counted(self):
        fake = '{"versions": {"2026.4.27": {}}}'
        self.assertEqual(_run_gap_python(self.code, fake), "0")

    def test_empty_stdin_prints_na(self):
        """CU-F3: 空响应 → NA（旧行为 except → print(0) 伪装成"没新版"）。"""
        self.assertEqual(_run_gap_python(self.code, ""), "NA")

    def test_garbage_json_prints_na(self):
        self.assertEqual(_run_gap_python(self.code, "<html>rate limited</html>"), "NA")

    def test_empty_versions_prints_na(self):
        """versions 为空 dict = 可疑响应（真 packument 必有 versions）→ NA 而非 0。"""
        self.assertEqual(_run_gap_python(self.code, '{"versions": {}}'), "NA")


class TestScriptBehaviorHermetic(unittest.TestCase):
    """B. 全脚本行为级（fakebin + 隔离 HOME，dev/Mac Mini 双环境 hermetic 零副作用）。"""

    def _run_script(self, npm_json=None, gh_json=None, cve_text=None, eval_days_ago=40):
        tmp = tempfile.mkdtemp(prefix="cu334_")
        home = os.path.join(tmp, "home")
        fakebin = os.path.join(tmp, "bin")
        os.makedirs(home)
        os.makedirs(fakebin)
        env = os.environ.copy()
        if npm_json is not None:
            p = os.path.join(tmp, "npm.json")
            with open(p, "w") as f:
                f.write(npm_json)
            env["FAKE_NPM_FILE"] = p
        else:
            env.pop("FAKE_NPM_FILE", None)
        if gh_json is not None:
            p = os.path.join(tmp, "gh.json")
            with open(p, "w") as f:
                f.write(gh_json)
            env["FAKE_GH_FILE"] = p
        else:
            env.pop("FAKE_GH_FILE", None)

        # fake curl: 按 URL 分发到 fixture 文件；支持 -o；无 fixture → exit 28（网络失败）
        curl_src = (
            "#!/bin/bash\n"
            'OUT=""\nURL=""\nprev=""\n'
            'for a in "$@"; do\n'
            '  if [ "$prev" = "-o" ]; then OUT="$a"; fi\n'
            '  case "$a" in http*) URL="$a";; esac\n'
            '  prev="$a"\n'
            "done\n"
            'emit() { if [ -n "$OUT" ]; then cat "$1" > "$OUT"; else cat "$1"; fi; }\n'
            'case "$URL" in\n'
            '  *registry.npmjs.org*) if [ -n "${FAKE_NPM_FILE:-}" ]; then emit "$FAKE_NPM_FILE"; exit 0; fi; exit 28;;\n'
            '  *api.github.com*) if [ -n "${FAKE_GH_FILE:-}" ]; then emit "$FAKE_GH_FILE"; exit 0; fi; exit 28;;\n'
            "esac\n"
            "exit 28\n"
        )
        # fake openclaw: (a) Mac Mini 跑本测试时屏蔽真 CLI（防 `plugins install` 真副作用）
        # (b) 让 DEV_MODE=false → tripped 路径的就绪检查段也被行为覆盖
        openclaw_src = (
            "#!/bin/bash\n"
            'if [ "${1:-}" = "--version" ]; then echo "2026.4.27"; exit 0; fi\n'
            'if [ "${1:-}" = "plugins" ]; then echo "Installed plugin (fake)"; exit 0; fi\n'
            "exit 0\n"
        )
        npm_src = "#!/bin/bash\necho fake-npm-response\nexit 0\n"
        for name, src in (("curl", curl_src), ("openclaw", openclaw_src), ("npm", npm_src)):
            p = os.path.join(fakebin, name)
            with open(p, "w") as f:
                f.write(src)
            os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        # fake notify.sh: 捕获告警调用（脚本 source $HOME/notify.sh）
        with open(os.path.join(home, "notify.sh"), "w") as f:
            f.write('notify() { printf "NOTIFY|%s\\n" "$*" >> "$HOME/notify_capture.txt"; return 0; }\n')

        if cve_text is not None:
            with open(os.path.join(home, ".openclaw_cve_alert"), "w") as f:
                f.write(cve_text)

        env["HOME"] = home
        env["PATH"] = fakebin + os.pathsep + env.get("PATH", "")
        # 时间 tripwire hermetic（否则 2027-01 后 LAST_EVAL_DATE 默认值会让全部测试假触发）
        env["OPENCLAW_LAST_EVAL_DATE"] = (date.today() - timedelta(days=eval_days_ago)).isoformat()
        r = subprocess.run(["bash", SCRIPT], capture_output=True, text=True, env=env, timeout=60)
        capture = os.path.join(home, "notify_capture.txt")
        captured = ""
        if os.path.isfile(capture):
            with open(capture, encoding="utf-8") as f:
                captured = f.read()
        return r, captured

    def test_network_down_reports_na_not_green(self):
        """CU-F3 血案回归: 全网络失败 → 2/3/4 显式不可判，绝不打 ✅ 0/50 假绿。"""
        r, captured = self._run_script()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("[2/6] 版本差距: 不可判", r.stdout)
        self.assertIn("[3/6] EOL 信号: 不可判", r.stdout)
        self.assertIn("[4/6] WhatsApp 破坏性: 不可判", r.stdout)
        self.assertIn("0/6 触发", r.stdout)
        self.assertIn("3 项网络不可判", r.stdout)
        self.assertNotIn("版本差距: 0/50", r.stdout, "旧 fail-green 形态复活（网络失败读作没新版）")
        self.assertNotIn("EOL 信号: latest release 未检出", r.stdout,
                         "旧 fail-green 形态复活（看不到 release 读作未检出）")
        self.assertEqual(captured, "", "无触发不得推送告警")

    def test_clean_run_all_green_no_notify(self):
        npm = '{"versions": {"2026.4.27": {}, "2026.4.28": {}}}'
        gh = '{"tag_name": "v2026.7.1-2", "body": "routine release notes"}'
        r, captured = self._run_script(npm_json=npm, gh_json=gh)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("✅ [2/6] 版本差距: 1/50", r.stdout)
        self.assertIn("✅ [3/6] EOL 信号: latest release 未检出", r.stdout)
        self.assertIn("✅ [4/6] WhatsApp 破坏性: latest release 未检出", r.stdout)
        self.assertIn("继续 hold", r.stdout)
        self.assertEqual(captured, "", "干净轮次不得推送")

    def test_eol_keyword_tracks_current_minor(self):
        """CU-F4: EOL 关键词从部署版本派生 — body 提及 v2026.4 → TRIPPED。"""
        npm = '{"versions": {"2026.4.27": {}}}'
        gh = '{"tag_name": "v2026.9.1", "body": "please note v2026.4 users read the migration guide"}'
        r, captured = self._run_script(npm_json=npm, gh_json=gh)
        self.assertEqual(r.returncode, 1, "tripped 路径必须非零退出")
        self.assertIn("🚨 [3/6] EOL 信号", r.stdout)
        self.assertIn("v2026.4", r.stdout)
        self.assertIn("触发", captured)

    def test_stale_v2026_3_keyword_retired(self):
        """CU-F4 反向: 只提 v2026.3 的 body 不再误触发（旧硬编码关键词已退役）。"""
        npm = '{"versions": {"2026.4.27": {}}}'
        gh = '{"tag_name": "v2026.9.1", "body": "changelog: backported a fix from the v2026.3 series"}'
        r, captured = self._run_script(npm_json=npm, gh_json=gh)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("✅ [3/6] EOL 信号: latest release 未检出", r.stdout)
        self.assertNotIn("🚨 [3/6]", r.stdout)

    def test_wa_breaking_section_still_trips(self):
        """tripwire-4 包裹 GH_OK 后内层逻辑不得被阉割（检出力不减）。"""
        npm = '{"versions": {"2026.4.27": {}}}'
        gh = ('{"tag_name": "v2026.9.1", '
              '"body": "## Breaking Changes\\n- removed whatsapp plugin compat flag\\n## Other\\n- misc"}')
        r, captured = self._run_script(npm_json=npm, gh_json=gh)
        self.assertEqual(r.returncode, 1)
        self.assertIn("🚨 [4/6] WhatsApp 破坏性", r.stdout)

    def test_cve_trip_pushes_notify_alert(self):
        """CU-F1 血案回归: tripwire 触发必须真推送（此前"触发推送告警"零机制支撑）。
        CVE 人工标记 = 零网络依赖的确定性触发路径。"""
        r, captured = self._run_script(cve_text="CVE-2026-9999 rce in gateway")
        self.assertEqual(r.returncode, 1)
        self.assertIn("🚨 [5/6] CVE 人工标记", r.stdout)
        self.assertIn("[SYSTEM_ALERT] OpenClaw 升级 tripwire 1/6 触发", captured)
        self.assertIn("--topic alerts", captured)
        self.assertIn("CVE", captured)
        self.assertIn("tripwire 告警已推送", r.stdout)

    def test_time_tripwire_local_backstop_still_works(self):
        """时间上限（本地计算，无网络依赖）是终极兜底 — 网络全断也必须能触发。"""
        r, captured = self._run_script(eval_days_ago=200)
        self.assertEqual(r.returncode, 1)
        self.assertIn("🚨 [1/6] 时间上限", r.stdout)
        self.assertIn("触发", captured)


class TestWiringGuards(unittest.TestCase):
    """C. CU-F1 wiring 跨文件契约 + 源码守卫。"""

    def test_registry_entry_present_and_shaped(self):
        """血案回归: check_upgrade 必须登记 jobs_registry（此前零登记 = 宪法违反）。"""
        import yaml
        data = yaml.safe_load(_read("jobs_registry.yaml"))
        jobs = {j["id"]: j for j in data.get("jobs", [])}
        self.assertIn("check_upgrade", jobs, "check_upgrade 未登记 jobs_registry.yaml")
        j = jobs["check_upgrade"]
        self.assertTrue(j.get("enabled"), "check_upgrade 必须 enabled")
        self.assertEqual(j.get("scheduler"), "system")
        self.assertEqual(j.get("entry"), "check_upgrade.sh")
        self.assertEqual(j.get("log"), "~/check_upgrade.log")
        fields = str(j.get("interval", "")).split()
        self.assertEqual(len(fields), 5, "interval 必须是 5 字段 cron 表达式")
        self.assertEqual(fields[4], "1", "每周一（文档声称的节奏）")

    def test_auto_deploy_filemap_syncs_script(self):
        """血案回归: FILE_MAP 必须含 check_upgrade.sh（此前运行时副本无人同步）。"""
        src = _read("auto_deploy.sh")
        self.assertIn('"check_upgrade.sh|$HOME/check_upgrade.sh"', src)

    def test_watchdog_log_freshness_covers_job(self):
        """血案回归: watchdog 必须覆盖 check_upgrade（此前它死了没人会发现）。
        阈值与 health_check/kb_trend 周任务惯例一致（跨条目一致性）。"""
        src = _read("job_watchdog.sh")
        m = re.search(r'"check_upgrade\|\$HOME/check_upgrade\.log\|(\d+)\|', src)
        self.assertIsNotNone(m, "check_upgrade 不在 LOG_FRESHNESS_JOBS")
        hc = re.search(r'"health_check\|\$HOME/health_check\.log\|(\d+)\|', src)
        self.assertIsNotNone(hc, "前提自检: health_check 条目存在")
        self.assertEqual(m.group(1), hc.group(1), "周任务阈值必须与 health_check 惯例一致 (14d)")

    def test_script_wires_notify_alerts_topic(self):
        """CU-F1: 可执行行必须 source notify.sh + 触发分支真调 notify --topic alerts。"""
        code = _executable_lines(_read("check_upgrade.sh"))
        self.assertIn('source "$HOME/notify.sh"', code)
        self.assertIn('notify "$ALERT_MSG" --topic alerts', code)

    def test_lexicographic_compare_retired(self):
        """CU-F2 源码守卫: 旧字典序形态与 '2026.' 前缀过滤必须退役（剥注释后扫）。"""
        code = _executable_lines(_read("check_upgrade.sh"))
        self.assertNotIn("if v > current", code, "字典序字符串比较回退")
        self.assertNotIn("startswith('2026.')", code, "年份前缀过滤回退（2027.1.x 漏计）")
        self.assertGreaterEqual(code.count("vkey("), 2, "数值元组比较 vkey 必须在位（def + 使用）")

    def test_na_plumbing_present(self):
        """CU-F3 源码守卫: NA 分支三件套（tripwire 2 赋值 / GH_OK 门 / NA 计数）在位。"""
        code = _executable_lines(_read("check_upgrade.sh"))
        self.assertIn('STABLE_AFTER="NA"', code)
        self.assertIn('if [ "$STABLE_AFTER" = "NA" ]; then', code)
        self.assertIn("GH_OK=false", code)
        self.assertGreaterEqual(code.count("TRIPWIRE_NA=$((TRIPWIRE_NA + 1))"), 3,
                                "2/3/4 三个网络 tripwire 各自要有 NA 计数")
        # 反向: tripwire-2 块内不得再有 || echo "0" 兜底（那是 fail-green 的根）
        start = code.index("Tripwire 2" if "Tripwire 2" in code else 'STABLE_AFTER="NA"')
        block = code[code.index("registry.npmjs.org"):code.index("api.github.com")]
        self.assertNotIn('echo "0"', block, "tripwire-2 网络路径不得用 0 伪装失败")

    def test_context_alert_branding_neutral(self):
        """rider 守卫: proxy_filters + job_watchdog 告警品牌去 Qwen 硬编码（一物一形，
        可执行行扫描 — 修复注释里保留了旧字面量作血案记录，剥注释防自咬）。"""
        for fname, expect_cnt in (("proxy_filters.py", 2), ("job_watchdog.sh", 2)):
            code = _executable_lines(_read(fname))
            self.assertNotIn("Qwen context", code, f"{fname} 可执行行仍含陈旧品牌")
            self.assertEqual(code.count("模型 context"), expect_cnt,
                             f"{fname} 应恰有 {expect_cnt} 处中性告警文案")

    def test_bash_syntax_valid(self):
        r = subprocess.run(["bash", "-n", SCRIPT], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_v37_9_334_marker(self):
        self.assertIn("V37.9.334", _read("check_upgrade.sh"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
