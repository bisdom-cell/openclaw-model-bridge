#!/usr/bin/env python3
"""
test_v37_9_325_incident_evidence.py — V37.9.325 对抗审计守卫

镜头（承接 V37.9.324「工具跑的是哪个模式」）：**故障发生时，我们收集到的证据真的存在吗？
每天注入 PA 的那份手册说的还是真的吗？**

三条 finding（全部 grounded 复现，非推测）：

  A（MED-HIGH，prompt 层 fail-plausible）kb_inject.sh 的 workspace CLAUDE.md heredoc
    硬编码「文本→Qwen3-235B，图片→Qwen2.5-VL-72B」——自 V37.9.222（2026-07-02）
    flip 到 doubao_21 起已陈旧 52 天。V37.9.243 做过 primary prose 全量收敛
    （CLAUDE/README/FEATURES/GUIDE/compat），**唯独漏了这一处，因为它躺在 .sh 里**，
    三个 doc-drift 机器检查（gen_readme_badges / gen_jobs_doc / gen_compat_matrix）
    只看 .md。而这份文件每天 07:00 重新生成并作为 PA 的操作手册进它的上下文，
    同一份文件里「三方共享意识」快照的「模型」行是活的（每小时刷新）——
    **同一事实在同一文件里一个活一个死，且死的那个更靠前** = 一物一形违反。
    修法按日落法：退役硬编码事实，指向已有的活快照（不新建 checker）。

  B（MED，故障取证）incident_snapshot.py 的 gateway 日志路径 ~/openclaw-gateway.log
    在本仓库**没有任何写入方**：launchd plist 写 ~/openclaw_gateway.log
    （deploy/install_openclaw_macmini.sh）/ restart.sh nohup fallback 写 ~/gateway.log /
    Gateway 自身写 /tmp/openclaw/openclaw-<date>.log（diagnose.sh 同源）。
    → README/FEATURES 声称的「三层日志自动采集」结构上只有两层，且缺的恰是
    gateway——它的静默死正是有案可查的故障类（踩坑 #96 / V37.8.13 宕 9h）。
    读者看到「file not found」会读成「gateway 没记日志」（对一个死掉的 gateway 而言
    这个错误结论比正确结论更可信）= fail-plausible。

  C（MED，故障取证）_service_status() 的 curl 只有 --connect-timeout 没有 --max-time，
    单服务由 subprocess timeout=5 兜底 → 三个**挂起**（接受连接但不响应）的服务
    实测耗时 15.0s，而 tool_proxy 侧 subprocess timeout=10 → **整份快照被杀掉**
    （_service_status 是写文件前的最后一步，已采集的日志一并丢弃）。
    即：快照机制恰好在它存在的那个场景（服务挂起）里失效。

反向验证（sabotage）见 changelog；本文件断言全部行为级优先，源码断言先剥注释行。
"""
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)


def _read(name):
    with open(os.path.join(REPO, name), encoding="utf-8") as f:
        return f.read()


def _strip_comment_lines(src):
    """剥掉 # 注释行（V37.9.178 家族：本次修复的注释里逐字写着被退役的字面量）。"""
    return "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))


def _service_status_body(src):
    """抽 _service_status 的执行体（去 docstring），源码断言只打在真实 argv 上。"""
    m = re.search(r"def _service_status\(\):(.*?)\n\n\ndef ", src, re.S)
    body = m.group(1) if m else ""
    return re.sub(r'"""circular"""', "", re.sub(r'""".*?"""', "", body, flags=re.S))


def _heredoc_blocks(src):
    """抽 kb_inject.sh 里写进 workspace CLAUDE.md 的 heredoc 正文（3 段）。"""
    return re.findall(r"<<\s*'?MDEOF'?\s*\n(.*?)\n\s*MDEOF\s*$", src, re.S | re.M)


# ════════════════════════════════════════════════════════════════════
# A: PA 操作手册不得硬编码会漂移的系统事实
# ════════════════════════════════════════════════════════════════════
class TestWorkspaceManualNoStaleFacts(unittest.TestCase):
    def setUp(self):
        self.src = _read("kb_inject.sh")
        self.blocks = _heredoc_blocks(self.src)

    def test_extraction_not_vacuous(self):
        """防空转：heredoc 必须真被抽到且含已知锚点，否则下面的断言全是空转。"""
        self.assertGreaterEqual(len(self.blocks), 3, "workspace CLAUDE.md heredoc 抽取失败")
        joined = "\n".join(self.blocks)
        self.assertIn("## 系统架构", joined)
        self.assertIn("## 运维命令", joined)
        self.assertGreater(len(joined), 2000)

    def test_arch_section_has_no_hardcoded_model_id(self):
        """血案回归：架构段不得再硬编码具体模型标识（V37.9.222 flip 后陈旧 52 天）。"""
        m = re.search(r"^## 系统架构\s*\n(.*?)(?=^## )", self.blocks[0], re.S | re.M)
        self.assertIsNotNone(m, "系统架构段抽取失败")
        arch = m.group(1)
        for token in ("Qwen3-235B", "Qwen2.5-VL", "Qwen3", "Doubao", "doubao_21", "DeepSeek"):
            self.assertNotIn(
                token, arch,
                f"PA 操作手册架构段硬编码模型标识 {token!r} —— 它会随 PROVIDER flip 陈旧, "
                f"且同一文件的「三方共享意识」快照已有活的模型行",
            )

    def test_arch_section_points_at_live_snapshot(self):
        """退役硬编码后必须留下可用指向，否则是删信息而非改信息。"""
        m = re.search(r"^## 系统架构\s*\n(.*?)(?=^## )", self.blocks[0], re.S | re.M)
        arch = m.group(1)
        self.assertIn("PROVIDER", arch, "架构段应说明 primary 由 PROVIDER env 配置（机制而非值）")
        self.assertRegex(arch, r"快照|status", "架构段应指向同文件内的实时快照")

    def test_live_model_snapshot_still_injected(self):
        """活快照本身仍必须被注入（本条是上一条的前提，防止指向一个不存在的东西）。"""
        self.assertIn("status_update.py", self.src)
        self.assertIn("--read --human", self.src)
        self.assertIn("三方共享意识", "\n".join(self.blocks))

    def test_no_hardcoded_lesson_count(self):
        """踩坑条数是会漂移的计数（GUIDE 现为 27，heredoc 曾写死 26）→ 去数字化。"""
        joined = "\n".join(self.blocks)
        hits = re.findall(r"\d+\s*条生产踩坑", joined)
        self.assertEqual(hits, [], f"PA 手册硬编码踩坑条数 {hits} —— 必然随 GUIDE 增补漂移")
        self.assertIn("GUIDE.md", joined, "防空转：GUIDE 指引本身仍应在手册里")


# ════════════════════════════════════════════════════════════════════
# B: 故障快照的 gateway 日志必须指向真实写入方
# ════════════════════════════════════════════════════════════════════
class TestGatewayLogResolution(unittest.TestCase):
    def setUp(self):
        import incident_snapshot
        self.mod = incident_snapshot

    def test_candidates_cover_every_writer_in_repo(self):
        """MR-8 跨文件契约：仓库里每一个 gateway 日志写入方都必须在候选表里。

        任一侧改动（改 plist StandardOutPath / 改 restart.sh 的 nohup 重定向）
        而候选表没跟 → 本条立即 fail。
        """
        cands = " ".join(self.mod.LOG_FILE_CANDIDATES["gateway"])

        deploy = _strip_comment_lines(_read(os.path.join("deploy", "install_openclaw_macmini.sh")))
        m = re.search(r"StandardOutPath</key>\s*\n\s*<string>\$HOME/([\w.\-]+)</string>", deploy)
        self.assertIsNotNone(m, "防空转：未能从 deploy 脚本抽出 launchd StandardOutPath")
        self.assertIn(m.group(1), cands, "launchd 写入的 gateway 日志不在候选表")

        restart = _strip_comment_lines(_read("restart.sh"))
        m2 = re.search(r"gateway --verbose\s*>>\s*~/([\w.\-]+)", restart)
        self.assertIsNotNone(m2, "防空转：未能从 restart.sh 抽出 nohup fallback 日志路径")
        self.assertIn(m2.group(1), cands, "restart.sh fallback 写入的 gateway 日志不在候选表")

        diag = _strip_comment_lines(_read("diagnose.sh"))
        self.assertIn("/tmp/openclaw/openclaw-", diag, "防空转：diagnose.sh 应引用 Gateway 自身日志")
        self.assertIn("/tmp/openclaw/openclaw-", cands, "Gateway 自身 verbose 日志不在候选表")

    def test_resolver_picks_first_existing(self):
        """行为级：候选按顺序解析，命中第一个真实存在的。"""
        with tempfile.TemporaryDirectory() as td:
            missing = os.path.join(td, "nope.log")
            present = os.path.join(td, "real.log")
            with open(present, "w") as f:
                f.write("gateway line\n")
            path, tried = self.mod._resolve_log_path([missing, present])
            self.assertEqual(path, present)
            self.assertEqual(tried, [missing, present])

    def test_resolver_reports_all_candidates_when_none_exists(self):
        """诚实：都不存在时要说清「找过哪些」，而不是只报一个错路径。"""
        with tempfile.TemporaryDirectory() as td:
            a, b = os.path.join(td, "a.log"), os.path.join(td, "b.log")
            path, tried = self.mod._resolve_log_path([a, b])
            self.assertIsNone(path)
            self.assertEqual(tried, [a, b])

    def test_snapshot_captures_launchd_style_gateway_log(self):
        """血案回归：只有 launchd 风格路径存在时，快照必须抓到内容而非 file not found。"""
        import json
        with tempfile.TemporaryDirectory() as td:
            gw = os.path.join(td, "openclaw_gateway.log")
            with open(gw, "w") as f:
                f.write("GATEWAY-EVIDENCE-LINE\n")
            old_c = self.mod.LOG_FILE_CANDIDATES
            old_d = self.mod.SNAPSHOT_DIR
            old_p = self.mod.SERVICE_PORTS
            try:
                self.mod.LOG_FILE_CANDIDATES = {"gateway": [os.path.join(td, "absent.log"), gw]}
                self.mod.SNAPSHOT_DIR = os.path.join(td, "snap")
                self.mod.SERVICE_PORTS = []   # 隔离：不去碰真实 5001/5002/18789
                path = self.mod.create_snapshot("test", "gateway evidence")
                self.assertIsNotNone(path)
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                self.assertIn("GATEWAY-EVIDENCE-LINE", data["logs"]["gateway"])
                self.assertEqual(data["logs_meta"]["gateway"]["resolved"], gw)
            finally:
                self.mod.LOG_FILE_CANDIDATES = old_c
                self.mod.SNAPSHOT_DIR = old_d
                self.mod.SERVICE_PORTS = old_p

    def test_retired_pathless_hardcode(self):
        """退役反模式：不得再用单一 dict 直接写死一条 gateway 路径。"""
        src = _strip_comment_lines(_read("incident_snapshot.py"))
        self.assertNotRegex(
            src, r'"gateway"\s*:\s*os\.path\.expanduser',
            "gateway 日志不得回退为单路径硬编码（本仓库无写入方产出 ~/openclaw-gateway.log）",
        )
        self.assertIn("LOG_FILE_CANDIDATES", src)


# ════════════════════════════════════════════════════════════════════
# C: 服务探测必须有硬预算，且不得超过调用方的 kill 超时
# ════════════════════════════════════════════════════════════════════
class TestServiceStatusBudget(unittest.TestCase):
    def setUp(self):
        import incident_snapshot
        self.mod = incident_snapshot

    @staticmethod
    def _hung_ports(n):
        socks, ports = [], []
        for _ in range(n):
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            s.listen(8)
            socks.append(s)
            ports.append(s.getsockname()[1])

        # 🔴 accept 出来的连接必须持有引用：不持有会被 GC 立刻关闭，curl 收到 EOF 秒返回，
        # 「挂起态服务」就退化成了「秒断服务」——本测试首版正是这样空转的（守卫自己抓到）。
        held = []

        def acceptor(s):
            while True:
                try:
                    conn, _ = s.accept()
                    held.append(conn)   # 接受后不响应且不关闭 = 真正的挂起态服务
                except OSError:
                    return
        for s in socks:
            threading.Thread(target=acceptor, args=(s,), daemon=True).start()
        return socks + held, ports

    def test_hung_services_stay_within_budget(self):
        """血案回归：三个挂起服务实测曾耗时 15.0s > 调用方 10s kill → 整份快照丢失。"""
        socks, ports = self._hung_ports(3)
        try:
            old = self.mod.SERVICE_PORTS
            self.mod.SERVICE_PORTS = list(zip(("adapter", "proxy", "gateway"), ports))
            t0 = time.monotonic()
            st = self.mod._service_status()
            elapsed = time.monotonic() - t0
        finally:
            self.mod.SERVICE_PORTS = old
            for s in socks:
                s.close()
        bound = self.mod.SERVICE_CHECK_BUDGET_SEC + self.mod.SERVICE_CHECK_TIMEOUT_SEC
        self.assertLess(elapsed, bound + 1.5, f"服务探测 {elapsed:.1f}s 超过硬上限 {bound}s")
        # 3 服务 × --max-time 2s 实测 ≈ 6.0s。放宽到 8s 容忍慢机器，但仍能抓住
        # 「去掉 --max-time、退回 subprocess timeout 兜底」的退化（实测 10.0s）。
        self.assertLess(elapsed, 8.0, f"挂起服务探测 {elapsed:.1f}s —— 单服务缺硬上限？（历史 15.0s / 无 --max-time 10.0s）")
        self.assertEqual(len(st), 3, "三个服务都要有条目（诚实标注而非静默缺失）")
        for name, v in st.items():
            self.assertTrue(v["http_code"], f"{name} 的探测结果不得为空串（无法区分'没响应'与'没探测'）")

    def test_budget_exhaustion_is_labeled_not_silent(self):
        """预算耗尽的服务必须显式标注，而不是静默缺失。

        用 BUDGET=0 确定性触发该分支（真实 3 服务 × 2s 不会耗尽 6s 预算，
        但服务数增加或单服务走满 subprocess 兜底时会）。
        """
        old_b = self.mod.SERVICE_CHECK_BUDGET_SEC
        old_p = self.mod.SERVICE_PORTS
        try:
            self.mod.SERVICE_CHECK_BUDGET_SEC = 0
            self.mod.SERVICE_PORTS = [("adapter", 1), ("proxy", 2)]
            st = self.mod._service_status()
        finally:
            self.mod.SERVICE_CHECK_BUDGET_SEC = old_b
            self.mod.SERVICE_PORTS = old_p
        self.assertEqual(len(st), 2, "预算耗尽也要给出全部条目")
        self.assertEqual({v["http_code"] for v in st.values()}, {"skipped_budget"})

    def test_healthy_service_still_detected(self):
        """检出力不减：正常服务照常拿到 HTTP 码。"""
        import http.server

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            old = self.mod.SERVICE_PORTS
            self.mod.SERVICE_PORTS = [("adapter", srv.server_address[1])]
            st = self.mod._service_status()
        finally:
            self.mod.SERVICE_PORTS = old
            srv.shutdown()
        self.assertEqual(st["adapter"]["http_code"], "200")

    def test_curl_has_max_time(self):
        """源码守卫：--connect-timeout 只管建连，挂起服务要靠 --max-time 才有界。

        🔴 断言必须落在**真实 argv 体**上：首版写成「整份源码里有 --max-time」，
        结果被 _service_status 自己的 docstring 满足（_strip_comment_lines 只剥 # 行
        不剥 docstring）→ sabotage 去掉 argv 里的 --max-time 时守卫没开火（空转）。
        """
        body = _service_status_body(_read("incident_snapshot.py"))
        self.assertIn("subprocess.run", body, "防空转：未抽到 _service_status 的执行体")
        self.assertIn("--max-time", body, "curl argv 缺 --max-time → 建连成功后可无限等")
        self.assertIn("--connect-timeout", body)


class TestCallerTimeoutExceedsSnapshotBudget(unittest.TestCase):
    def test_tool_proxy_timeout_strictly_greater(self):
        """跨文件契约：调用方的 kill 超时必须严格大于快照自身最坏耗时。

        由 incident_snapshot 的常量算出预期下界，不写死数字——将来任一侧调参，
        两者关系失效时本条立即 fail（正是本次 10s vs 15s 的成因）。
        """
        import incident_snapshot as inc
        worst = inc.SERVICE_CHECK_BUDGET_SEC + inc.SERVICE_CHECK_TIMEOUT_SEC
        # 服务探测之外还要跑完：解释器启动 + 三份日志尾读 + proxy_stats 读 + JSON 写盘 +
        # 旧快照清理。仅仅「大于最坏探测耗时」等于零余量（10 > 9 也成立，但快照会卡在
        # 写盘那一刻被杀）——本函数跑在 daemon 线程里不阻塞请求，余量是免费的。
        OVERHEAD_MARGIN_SEC = 10
        src = _strip_comment_lines(_read("tool_proxy.py"))
        m = re.search(
            r"snapshot_script,\s*\"--auto\",\s*description\],?\s*\n\s*timeout=(\d+)",
            src,
        )
        self.assertIsNotNone(m, "防空转：未能从 tool_proxy 抽出快照子进程 timeout")
        self.assertGreaterEqual(
            int(m.group(1)), worst + OVERHEAD_MARGIN_SEC,
            f"tool_proxy kill 超时 {m.group(1)}s 未给快照留出余量 "
            f"（最坏服务探测 {worst}s + 写盘/尾读余量 {OVERHEAD_MARGIN_SEC}s）→ 快照可能被杀掉",
        )


class TestSourceGuards(unittest.TestCase):
    def test_markers(self):
        for name in ("incident_snapshot.py", "kb_inject.sh"):
            self.assertIn("V37.9.325", _read(name), f"{name} 缺 V37.9.325 血案 marker")


if __name__ == "__main__":
    unittest.main(verbosity=2)
