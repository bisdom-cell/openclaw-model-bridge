#!/usr/bin/env python3
"""V37.9.365 — 周报/诊断的「模型」检查测的是主力，不是 fallback 末位。

血案（2026-09-30 开工对抗巡查发现，V37.9.222 flip 起潜伏约三个月）:
  health_check.sh（每周一 09:00 周报）与 diagnose.sh【3/7】的模型检查是 V27 逻辑——
  拿 Qwen3 远端 /models 里第一个含 'Qwen3' 的 ID，与 openclaw.json 的 qwen-local 标签比对。
  V37.9.222 起生产主力是 doubao_21（PROVIDER env），Qwen3 只是 FALLBACK_ORDER 的末位，
  qwen-local/ 只是 Gateway 路由标签（adapter 重建请求体时整体覆盖 model）。结果:
    - 周报每周一报「🤖 模型: 🟢 Qwen3-…」或「❓ 检查不可用」——测的是第 4 跳 fallback 端点;
    - 主力 doubao_21 宕机、断路器 OPEN 时这一行照样绿（它根本不看主力）;
    - diagnose 被注释为「多任务同时失败的第一反应」，却在 Qwen3 端点不可达时判 FAIL、
      主力宕机时判 ✅——两个方向都错。
  同一事实（谁是主力、它健不健康）在 kb_status_refresh / preflight 里早就读 adapter /health，
  这两个脚本是仅存的第二来源 = 一物两形（V37.9.243/325 同族: 机器检查只看 .md, 脚本里的
  硬编码事实没人对账）。

守卫分四类:
  A. 周报模型行行为级（从 health_check.sh 真源码抽块, 假 curl 喂 /health, 真 bash 跑）
  B. diagnose【3/7】行为级（同法, 且在脚本自己的 set -eo pipefail 下跑）
  C. 单一真理源契约（adapter /health 的键 == 两个脚本消费的键; 旧 Qwen3 远端比对退役）
  D. health_status.json 机器可读契约随之更新（跑完整脚本, 隔离 push）
"""
import json
import os
import re
import stat
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
HEALTH_SH = os.path.join(REPO, "health_check.sh")
DIAG_SH = os.path.join(REPO, "diagnose.sh")
ADAPTER_PY = os.path.join(REPO, "adapter.py")

SECRET_MODEL = "ep-20990101000000-secretfake"
CHAIN = ["deepseek_full", "doubao_21_tokenhub", "deepseek", "qwen"]


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _code_lines(src):
    """去掉 # 注释行（血案注释里逐字写着被退役的形态, V37.9.178 家族）。"""
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))


def _slice(src, start_marker, end_marker):
    i = src.find(start_marker)
    j = src.find(end_marker, i + 1)
    if i < 0 or j < 0:
        raise AssertionError(f"marker not found: {start_marker!r} .. {end_marker!r}")
    return src[i:j]


def _health_payload(provider="doubao_21", cb="closed", chain=None, ok=True, model=SECRET_MODEL):
    d = {"ok": ok, "version": "0.37.9.184", "provider": provider, "model": model}
    chain = CHAIN if chain is None else chain
    if chain:
        d["fallback_chain"] = chain
        d["fallback"] = chain[0]
        if cb is not None:
            d["circuit_breaker"] = cb
    return json.dumps(d)


class _FakeCurlMixin:
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="v365_")
        self.bindir = os.path.join(self._tmp, "bin")
        os.makedirs(self.bindir)
        curl = os.path.join(self.bindir, "curl")
        with open(curl, "w") as f:
            f.write('#!/bin/bash\n'
                    'if [ "${FAKE_CURL_FAIL:-0}" = "1" ]; then exit 7; fi\n'
                    'printf "%s" "$FAKE_HEALTH"\n')
        os.chmod(curl, os.stat(curl).st_mode | stat.S_IEXEC)

    def tearDown(self):
        subprocess.run(["rm", "-rf", self._tmp])

    def _env(self, payload=None, curl_fail=False):
        env = os.environ.copy()
        env["PATH"] = self.bindir + os.pathsep + env.get("PATH", "")
        env["FAKE_HEALTH"] = payload or ""
        env["FAKE_CURL_FAIL"] = "1" if curl_fail else "0"
        return env


class TestHealthCheckModelLine(_FakeCurlMixin, unittest.TestCase):
    """A. 周报「🤖 模型」行: 报主力 + fallback 链 + 断路器, 跟随 adapter 而非硬编码 Qwen3。"""

    def _run(self, payload=None, curl_fail=False):
        block = _slice(_read(HEALTH_SH), "# === 1b.", "# === 2.")
        script = ("set -eo pipefail\n" + block +
                  'printf "LINE=%s\\n" "$model_line"\n'
                  'printf "PRIMARY=%s\\n" "$MODEL_PRIMARY"\n'
                  'printf "CHAIN=%s\\n" "$MODEL_CHAIN"\n'
                  'printf "CB=%s\\n" "$MODEL_CB"\n'
                  'printf "REACH=%s\\n" "$MODEL_REACHABLE"\n')
        p = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                           timeout=30, env=self._env(payload, curl_fail))
        self.assertEqual(p.returncode, 0, p.stderr)
        out = {}
        for line in p.stdout.splitlines():
            k, _, v = line.partition("=")
            out[k] = v
        return out, p.stdout

    def test_block_is_real_and_non_trivial(self):
        """防空转: 抽到的块必须真的读 adapter /health 并产出 model_line。"""
        block = _slice(_read(HEALTH_SH), "# === 1b.", "# === 2.")
        self.assertIn("localhost:5001/health", block)
        self.assertIn("model_line=", block)

    def test_primary_closed_reports_primary_and_chain(self):
        out, _ = self._run(_health_payload())
        line = out["LINE"]
        self.assertTrue(line.startswith("🤖 模型: 🟢"), line)
        self.assertIn("主力 doubao_21", line)
        self.assertIn("fallback 4 跳", line)
        self.assertIn("deepseek_full → doubao_21_tokenhub → deepseek → qwen", line)
        self.assertIn("断路器 closed", line)
        self.assertEqual(out["PRIMARY"], "doubao_21")
        self.assertEqual(out["CHAIN"], ",".join(CHAIN))
        self.assertEqual(out["CB"], "closed")
        self.assertEqual(out["REACH"], "1")

    def test_blood_lesson_primary_not_qwen(self):
        """血案回归: 主力是 doubao_21 时, 模型行不得把 Qwen 报成当前模型。"""
        out, _ = self._run(_health_payload())
        self.assertNotIn("Qwen3", out["LINE"])
        self.assertFalse(out["LINE"].startswith("🤖 模型: 🟢 Qwen"), out["LINE"])
        self.assertNotIn("主力 qwen", out["LINE"])

    def test_follows_adapter_not_hardcoded(self):
        """whiplash-resistant: 同一代码, 主力换成谁就报谁（未来再 flip 不必改脚本）。"""
        out, _ = self._run(_health_payload(provider="qwen", chain=["deepseek_full"]))
        self.assertIn("主力 qwen", out["LINE"])
        out2, _ = self._run(_health_payload(provider="deepseek_full", chain=["qwen"]))
        self.assertIn("主力 deepseek_full", out2["LINE"])

    def test_breaker_open_is_red(self):
        """主力连续失败（断路器 OPEN）必须在周报里变红——旧逻辑结构上看不到这件事。"""
        out, _ = self._run(_health_payload(cb="open"))
        self.assertTrue(out["LINE"].startswith("🤖 模型: 🔴"), out["LINE"])
        self.assertIn("断路器 OPEN", out["LINE"])
        self.assertEqual(out["CB"], "open")

    def test_breaker_half_open_is_yellow(self):
        out, _ = self._run(_health_payload(cb="half-open"))
        self.assertTrue(out["LINE"].startswith("🤖 模型: 🟡"), out["LINE"])

    def test_adapter_unreachable_is_red_not_question_mark(self):
        out, _ = self._run(curl_fail=True)
        self.assertTrue(out["LINE"].startswith("🤖 模型: 🔴"), out["LINE"])
        self.assertIn("无响应", out["LINE"])
        self.assertEqual(out["REACH"], "0")
        self.assertEqual(out["PRIMARY"], "")

    def test_ok_false_or_garbage_body_is_red(self):
        for body in ('{"ok": false}', "<html>502</html>", ""):
            out, _ = self._run(body)
            self.assertTrue(out["LINE"].startswith("🤖 模型: 🔴"), (body, out["LINE"]))

    def test_no_chain_stated_not_hidden(self):
        """H1-C 第二实例可能不配 fallback 链: 如实说, 不崩。"""
        out, _ = self._run(_health_payload(provider="qwen", chain=[]))
        self.assertIn("无 fallback 链", out["LINE"])
        self.assertTrue(out["LINE"].startswith("🤖 模型: 🟢"), out["LINE"])

    def test_model_id_never_rendered(self):
        """doubao_21 的 model 是 Volcengine 接入点 ID（V37.9.216 类机密）, 不进推送正文。"""
        for cb in ("closed", "open", "half-open"):
            _, raw = self._run(_health_payload(cb=cb))
            self.assertNotIn(SECRET_MODEL, raw)
            self.assertNotIn("ep-", raw)


class TestDiagnoseRoute(_FakeCurlMixin, unittest.TestCase):
    """B. diagnose【3/7】: 断路器 OPEN / adapter 不可达 → FAIL; 主力正常 → 不误报。"""

    def _run(self, payload=None, curl_fail=False):
        block = _slice(_read(DIAG_SH), "# ── 3.", "# ── 4.")
        script = ("set -eo pipefail\nFAIL=0\n" + block +
                  'echo "FAIL=$FAIL"\necho "DONE"\n')
        p = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                           timeout=30, env=self._env(payload, curl_fail))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("DONE", p.stdout, "块不得在 set -eo pipefail 下中途退出")
        return p.stdout

    def test_block_is_real(self):
        block = _slice(_read(DIAG_SH), "# ── 3.", "# ── 4.")
        self.assertIn("localhost:5001/health", block)
        self.assertIn("FAIL=1", block)

    def test_primary_healthy_no_false_fail(self):
        out = self._run(_health_payload())
        self.assertIn("FAIL=0", out)
        self.assertIn("主力: doubao_21", out)
        self.assertIn("deepseek_full → doubao_21_tokenhub → deepseek → qwen", out)
        self.assertIn("断路器 closed", out)

    def test_breaker_open_fails(self):
        out = self._run(_health_payload(cb="open"))
        self.assertIn("FAIL=1", out)
        self.assertIn("OPEN", out)

    def test_adapter_unreachable_fails(self):
        out = self._run(curl_fail=True)
        self.assertIn("FAIL=1", out)
        self.assertIn("无响应", out)

    def test_half_open_not_fail(self):
        """half-open = 正在恢复, 提示但不判失败（断路器自己在处理）。"""
        out = self._run(_health_payload(cb="half-open"))
        self.assertIn("FAIL=0", out)
        self.assertIn("half-open", out)

    def test_model_id_never_printed(self):
        """排障输出常被整段贴进聊天, 不带接入点 ID。"""
        for cb in ("closed", "open"):
            out = self._run(_health_payload(cb=cb))
            self.assertNotIn(SECRET_MODEL, out)
            self.assertNotIn("ep-", out)


class TestSingleSourceContract(unittest.TestCase):
    """C. 「谁是主力」只有一个来源: adapter /health。"""

    def _adapter_health_block(self):
        src = _read(ADAPTER_PY)
        i = src.find('if self.path in ("/health", "/v1/health"):')
        self.assertGreater(i, 0, "adapter /health 处理分支找不到")
        return src[i:i + 1500]

    def test_adapter_health_exposes_consumed_keys(self):
        """MR-8 跨文件契约: 两个脚本消费的键必须真由 adapter /health 产出。"""
        blk = self._adapter_health_block()
        self.assertRegex(blk, r'info = \{"ok": True,[^}]*"provider": PROVIDER_NAME')
        self.assertIn('info["fallback_chain"]', blk)
        self.assertIn('info["circuit_breaker"] = _circuit_breaker.state()', blk)

    def test_consumers_read_keys_adapter_produces(self):
        for path in (HEALTH_SH, DIAG_SH):
            code = _code_lines(_read(path))
            for key in ('"ok"', '"provider"', '"fallback_chain"', '"circuit_breaker"'):
                self.assertIn(f"d.get({key}", code, f"{os.path.basename(path)} 未读 {key}")

    def test_breaker_state_values_match_adapter(self):
        """脚本判的三个断路器值必须是 adapter CircuitBreaker.state() 真会返回的值。"""
        src = _read(ADAPTER_PY)
        for v in ("closed", "open", "half-open"):
            self.assertIn(f'"{v}"', src, f"adapter 不再产出断路器状态 {v!r}")

    def test_qwen_remote_comparison_retired(self):
        """旧 V27 逻辑退役: 不再按 'Qwen3' 过滤远端模型、不再读 qwen-local 标签当本地模型。"""
        for path in (HEALTH_SH, DIAG_SH):
            code = _code_lines(_read(path))
            self.assertNotIn("'Qwen3' in m['id']", code, path)
            self.assertNotIn("['qwen-local']", code, path)
            self.assertNotRegex(code, r'REMOTE_BASE_URL[^\n]*\}/models', path)

    def test_every_model_truth_reader_uses_adapter_health(self):
        """周报 / 诊断 / 小时刷新 / 体检 四个读者都指向同一端点（一物一形）。"""
        for name in ("health_check.sh", "diagnose.sh", "kb_status_refresh.sh", "preflight_check.sh"):
            code = _code_lines(_read(os.path.join(REPO, name)))
            self.assertIn("http://localhost:5001/health", code, name)

    def test_launchd_labels_in_advice_exist_in_registry(self):
        """排障建议里写的 launchd 标签必须是 services_registry.yaml 真登记的。

        同批发现: diagnose.sh 建议「launchctl unload/load com.openclaw.gateway.plist」、
        cron_doctor.sh 建议「launchctl kickstart -k system/com.openclaw.gateway」——真实标签是
        ai.openclaw.gateway（用户域）, 故障时照抄的恢复命令必然失败。扫全部运行时 .sh, 不写死名单。
        """
        reg = _read(os.path.join(REPO, "services_registry.yaml"))
        labels = set(re.findall(r"^\s*label:\s*(\S+)", reg, re.M))
        self.assertIn("ai.openclaw.gateway", labels, "防空转: registry 标签应能解析出来")
        seen, bad = 0, []
        for dirpath, dirs, files in os.walk(REPO):
            dirs[:] = [d for d in dirs if d not in (".git", "node_modules")]
            for fn in files:
                if not fn.endswith(".sh") or fn.startswith("test_"):
                    continue
                path = os.path.join(dirpath, fn)
                for i, line in enumerate(_read(path).splitlines(), 1):
                    if line.lstrip().startswith("#"):
                        continue
                    for tok in re.findall(r"\b(?:com|ai)\.openclaw\.[a-z_]+", line):
                        seen += 1
                        if tok not in labels:
                            bad.append(f"{os.path.relpath(path, REPO)}:{i} {tok}")
        self.assertGreaterEqual(seen, 6, "防空转: 扫描应能看到 restart.sh 等处的真实标签")
        self.assertEqual(bad, [], "脚本里写了 registry 没登记的 launchd 标签")

    def test_bash_syntax(self):
        for path in (HEALTH_SH, DIAG_SH):
            p = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)


class TestHealthStatusJson(_FakeCurlMixin, unittest.TestCase):
    """D. health_status.json 的 model 字段随之换成主力路由（旧 remote/local/match 退役）。"""

    def test_full_script_json_model_section(self):
        env = self._env(_health_payload(cb="open"))
        tmpd = self._tmp
        env["OPENCLAW_REPO_DIR"] = REPO
        env["HOME"] = os.path.join(tmpd, "home")
        os.makedirs(env["HOME"])
        env["HEALTH_JSON_PATH"] = os.path.join(tmpd, "health_status.json")
        marker = os.path.join(tmpd, "push.marker")
        open(marker, "w").close()
        env["HEALTH_PUSH_MARKER"] = marker
        env["HEALTH_PUSH_MIN_INTERVAL_SEC"] = "999999999"
        env["OPENCLAW_BIN"] = "/usr/bin/true"
        env["OPENCLAW"] = "/usr/bin/true"
        env["HEALTH_INFLUENCE_RECORD"] = "0"
        p = subprocess.run(["bash", HEALTH_SH], capture_output=True, text=True,
                           timeout=180, env=env)
        self.assertIn("🤖 模型: 🔴", p.stdout)
        self.assertNotIn(SECRET_MODEL, p.stdout)
        with open(env["HEALTH_JSON_PATH"], encoding="utf-8") as f:
            data = json.load(f)
        m = data["model"]
        self.assertEqual(m["primary"], "doubao_21")
        self.assertEqual(m["fallback_chain"], CHAIN)
        self.assertEqual(m["circuit_breaker"], "open")
        self.assertIs(m["reachable"], True)
        for gone in ("remote", "local", "match"):
            self.assertNotIn(gone, m)
        self.assertNotIn(SECRET_MODEL, json.dumps(data))


if __name__ == "__main__":
    unittest.main()
