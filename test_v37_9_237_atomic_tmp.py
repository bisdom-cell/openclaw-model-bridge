#!/usr/bin/env python3
"""test_v37_9_237_atomic_tmp.py — 审计 finding C 安全半边守卫

背景（V37.9.237）：status.json / ~/.kb/index.json 的原子写此前用**固定** tmp 名
（`TARGET + ".tmp"`）。两个并发写者（~40 cron 进程 / kb_dedup vs kb_autotag）写
同一个固定 tmp 路径 → 字节交错 → os.replace 发布损坏文件 → 读者 JSONDecodeError →
fallback DEFAULT_STATUS / {"entries": []}（系统失忆 / KB 索引失忆）。

修复：tmp 名带 pid 后缀（`"{}.tmp.{}".format(TARGET, os.getpid())`）。并发写者（不同
进程 = 不同 pid）写各自 tmp 路径，绝不共用 → 消除交错损坏。os.replace 仍保证 atomic
publish，并发下最坏是 last-writer-wins（良性丢更新）。

**V37.9.238 第二半边（同 finding C）**：status_lock() 跨写者 RMW 锁（fcntl.flock
独立锁文件 + 有界获取 + FAIL-OPEN 超时无锁继续 = 结构性无死锁）——防并发
load→mutate→save 互相覆盖（lost-update）；audit_log.audit() 同款锁防链 fork
（两并发读同 last-hash → 同 prev → verify_chain 假 tamper）。
TestStatusLockV238 / TestAuditChainLockV238 覆盖第二半边（并发全保留现在是
**确定性**断言——锁串行化所有写者）。
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.abspath(__file__))


def _load(modname, path):
    spec = importlib.util.spec_from_file_location(modname, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------------------
# status.json — 最关键共享状态（三方意识锚点，~40 并发写者，损坏=系统失忆）
# ---------------------------------------------------------------------------
class TestStatusTmpUniqueness(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.status = os.path.join(self.tmpdir, "status.json")
        self.su = _load("status_update", os.path.join(REPO, "status_update.py"))
        self.su.STATUS_FILE = self.status

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _capture_tmp_path(self, pid):
        """在给定 pid 下调用 save_status，返回它写入并发布的 tmp 路径。"""
        captured = {}
        real_replace = os.replace

        def spy_replace(src, dst):
            captured["src"] = src
            return real_replace(src, dst)

        with mock.patch("os.getpid", return_value=pid), \
             mock.patch("os.replace", side_effect=spy_replace):
            self.su.save_status({"priorities": [pid]}, updated_by="test")
        return captured["src"]

    def test_tmp_path_contains_pid(self):
        p = self._capture_tmp_path(4242)
        self.assertTrue(p.endswith(".tmp.4242"), p)
        # 发布后 tmp 已被 os.replace 重命名，不残留
        self.assertFalse(os.path.exists(p))

    def test_two_pids_distinct_tmp_paths(self):
        """核心属性：两个并发写者（不同 pid）用不同 tmp 路径 → 不可能交错损坏。"""
        p1 = self._capture_tmp_path(111)
        p2 = self._capture_tmp_path(222)
        self.assertNotEqual(p1, p2)
        self.assertTrue(p1.endswith(".tmp.111"))
        self.assertTrue(p2.endswith(".tmp.222"))

    def test_published_file_valid_json(self):
        self.su.save_status({"priorities": ["x"]}, updated_by="test")
        with open(self.status) as f:
            data = json.load(f)  # 绝不 JSONDecodeError
        self.assertEqual(data["priorities"], ["x"])
        self.assertEqual(data["updated_by"], "test")

    def test_write_exception_cleans_up_tmp(self):
        """写入异常 → finally 清理 tmp，不留 orphan（唯一 tmp 的 orphan 防累积）。"""
        with mock.patch.object(self.su.json, "dump", side_effect=ValueError("boom")):
            with self.assertRaises(ValueError):
                self.su.save_status({"priorities": []}, updated_by="test")
        leftovers = [f for f in os.listdir(self.tmpdir) if ".tmp." in f]
        self.assertEqual(leftovers, [], "写入异常后未清理 tmp orphan: %s" % leftovers)

    def test_real_subprocess_concurrency_no_corruption(self):
        """真跨进程并发 sanity：N 个 status_update.py 子进程并发 --add，
        最终文件必须始终 valid JSON（never JSONDecodeError）。

        诚实：这证明"无损坏 + 无 crash"，不证明"所有 insert 保留"（last-writer-wins
        丢更新是登记未修的 lost-update 半边）。故只断言 valid JSON + 是 dict，不断言 count。
        """
        home = os.path.join(self.tmpdir, "home")
        kb = os.path.join(home, ".kb")
        os.makedirs(kb, exist_ok=True)
        # 预置一个合法 status.json，让子进程解析到 ~/.kb/status.json
        with open(os.path.join(kb, "status.json"), "w") as f:
            json.dump({"recent_changes": []}, f)
        env = os.environ.copy()
        env["HOME"] = home
        procs = []
        for i in range(8):
            item = json.dumps({"date": "2026-07-03", "what": "c%d" % i, "by": "test"})
            procs.append(subprocess.Popen(
                [sys.executable, os.path.join(REPO, "status_update.py"),
                 "--add", "recent_changes", item, "--by", "test"],
                env=env, cwd=REPO,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        for p in procs:
            p.wait()
        # 最终文件必须 valid JSON（并发写从未损坏它）
        with open(os.path.join(kb, "status.json")) as f:
            data = json.load(f)  # 不抛 JSONDecodeError = 无损坏
        self.assertIsInstance(data, dict)
        self.assertIsInstance(data.get("recent_changes"), list)
        # 无 tmp orphan 累积（子进程正常退出）
        orphans = [f for f in os.listdir(kb) if f.startswith("status.json.tmp.")]
        self.assertEqual(orphans, [], "子进程留下 tmp orphan: %s" % orphans)


# ---------------------------------------------------------------------------
# ~/.kb/index.json — kb_dedup / kb_autotag 无锁写者（finding C "kb_write vs kb_dedup"）
# ---------------------------------------------------------------------------
class TestKbIndexTmpUniqueness(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.index = os.path.join(self.tmpdir, "index.json")
        self.dedup = _load("kb_dedup", os.path.join(REPO, "kb_dedup.py"))
        self.dedup.INDEX_FILE = self.index

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _capture_tmp(self, pid):
        captured = {}
        real_replace = os.replace

        def spy_replace(src, dst):
            captured["src"] = src
            return real_replace(src, dst)

        with mock.patch("os.getpid", return_value=pid), \
             mock.patch("os.replace", side_effect=spy_replace):
            self.dedup.save_index({"entries": [pid]})
        return captured["src"]

    def test_index_tmp_contains_pid(self):
        p = self._capture_tmp(7777)
        self.assertTrue(p.endswith(".tmp.7777"), p)

    def test_index_two_pids_distinct(self):
        self.assertNotEqual(self._capture_tmp(1), self._capture_tmp(2))

    def test_index_valid_json_after_write(self):
        self.dedup.save_index({"entries": [{"k": "v"}]})
        with open(self.index) as f:
            data = json.load(f)
        self.assertEqual(data["entries"], [{"k": "v"}])


# ---------------------------------------------------------------------------
# 源码守卫（反向验证：回退到固定 tmp 名 → 守卫立即 fail）
# ---------------------------------------------------------------------------
class TestSourceGuards(unittest.TestCase):
    def _read(self, name):
        with open(os.path.join(REPO, name)) as f:
            return f.read()

    def test_status_update_pid_tmp(self):
        c = self._read("status_update.py")
        self.assertIn('"{}.tmp.{}".format(STATUS_FILE, os.getpid())', c)
        self.assertNotIn('tmp = STATUS_FILE + ".tmp"', c)  # 固定名反模式已退役

    def test_kb_dedup_pid_tmp(self):
        c = self._read("kb_dedup.py")
        self.assertIn('"{}.tmp.{}".format(INDEX_FILE, os.getpid())', c)
        self.assertNotIn('tmp = INDEX_FILE + ".tmp"', c)

    def test_kb_autotag_pid_tmp(self):
        c = self._read("kb_autotag.py")
        self.assertIn('"{}.tmp.{}".format(INDEX_FILE, os.getpid())', c)
        self.assertNotIn('tmp = INDEX_FILE + ".tmp"', c)

    def test_finally_cleanup_present(self):
        # 三处都必须有 finally 清理未发布 tmp（防 orphan 累积）
        for name in ("status_update.py", "kb_dedup.py", "kb_autotag.py"):
            c = self._read(name)
            self.assertIn("os.remove(tmp)", c, "%s 缺 finally tmp 清理" % name)

    def test_honest_docstring_not_fail_plausible(self):
        # docstring 不得再声称"支持并发安全"（fail-plausible）；须诚实标注 non-lost-update
        c = self._read("status_update.py")
        self.assertNotIn("支持并发安全", c)
        self.assertIn("last-writer-wins", c)

    def test_v37_9_237_marker(self):
        for name in ("status_update.py", "kb_dedup.py", "kb_autotag.py"):
            self.assertIn("V37.9.237", self._read(name))


# ---------------------------------------------------------------------------
# V37.9.238 第二半边 — status_lock() 跨写者 RMW 锁（lost-update）
# ---------------------------------------------------------------------------
class TestStatusLockV238(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.su = _load("status_update_lock", os.path.join(REPO, "status_update.py"))
        self.su.STATUS_FILE = os.path.join(self.tmpdir, "status.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _spawn_holder(self, hold_sec):
        """子进程持有同一锁文件 hold_sec 秒（真跨进程竞争）。"""
        code = (
            "import fcntl, os, time, sys\n"
            "fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o644)\n"
            "fcntl.flock(fd, fcntl.LOCK_EX)\n"
            "print('HELD', flush=True)\n"
            "time.sleep(float(sys.argv[2]))\n"
        )
        p = subprocess.Popen(
            [sys.executable, "-c", code, self.su.STATUS_FILE + ".lock", str(hold_sec)],
            stdout=subprocess.PIPE, text=True)
        p.stdout.readline()  # 等 HELD
        return p

    def test_mutual_exclusion_waits_for_holder(self):
        """真跨进程互斥：holder 持锁 1.2s，status_lock 应等待后真获取。"""
        import time as _t
        holder = self._spawn_holder(1.2)
        t0 = _t.time()
        with self.su.status_lock(timeout=5.0) as acquired:
            waited = _t.time() - t0
        holder.wait()
        self.assertTrue(acquired, "holder 释放后应真获取锁")
        self.assertGreater(waited, 0.8, "应真等待 holder（非立即假获取）")

    def test_fail_open_on_timeout_never_blocks(self):
        """结构性无死锁核心：holder 持锁超 timeout → fail-open 无锁继续。"""
        import time as _t
        holder = self._spawn_holder(10)
        try:
            t0 = _t.time()
            with self.su.status_lock(timeout=0.5) as acquired:
                waited = _t.time() - t0
            self.assertFalse(acquired, "超时应 fail-open（acquired=False）")
            self.assertLess(waited, 2.0, "超时后必须立即继续，绝不阻塞")
        finally:
            holder.kill()
            holder.wait()

    def test_concurrent_cli_rmw_all_preserved(self):
        """lost-update 修复端到端（确定性）：8 并发 CLI --add 全部保留。

        V37.9.237 时此断言不可能（无锁 last-writer-wins 会丢），锁串行化后确定性成立。
        """
        home = os.path.join(self.tmpdir, "home")
        kb = os.path.join(home, ".kb")
        os.makedirs(kb, exist_ok=True)
        with open(os.path.join(kb, "status.json"), "w") as f:
            json.dump({"recent_changes": []}, f)
        env = os.environ.copy()
        env["HOME"] = home
        procs = []
        for i in range(8):
            item = json.dumps({"date": "2026-07-03", "what": "lk%d" % i, "by": "t"})
            procs.append(subprocess.Popen(
                [sys.executable, os.path.join(REPO, "status_update.py"),
                 "--add", "recent_changes", item, "--by", "test"],
                env=env, cwd=REPO,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        for p in procs:
            p.wait()
        with open(os.path.join(kb, "status.json")) as f:
            data = json.load(f)
        whats = sorted(rc["what"] for rc in data["recent_changes"])
        self.assertEqual(whats, ["lk%d" % i for i in range(8)],
                         "并发 RMW 丢更新: %s" % whats)

    def test_source_guards(self):
        with open(os.path.join(REPO, "status_update.py")) as f:
            c = f.read()
        # main() 的 RMW 必须在锁内执行——断言两行连续代码形态（防守卫被 status_lock
        # docstring 里的用法示例满足 = V37.9.178 "守卫被自己的注释咬" 同族陷阱）。
        self.assertIn("with status_lock():\n        _run_rmw(args, parser)", c,
                      "main() RMW 未在 status_lock 内（或形态被改）")
        # save_status 自身不得内嵌锁（同进程二次 flock 自阻塞 → 假超时）
        save_body = c.split("def save_status(")[1].split("\ndef ")[0]
        self.assertNotIn("status_lock", save_body,
                         "save_status 不得内嵌 status_lock（自阻塞风险）")
        # 三个库 RMW 调用方必须包锁（含部署窗口 fallback）
        for mod in ("daily_observer.py", "preference_learner.py", "security_score.py"):
            with open(os.path.join(REPO, mod)) as f:
                mc = f.read()
            self.assertIn("status_lock", mc, "%s RMW 未包锁" % mod)
            self.assertIn("nullcontext", mc, "%s 缺部署窗口 fallback" % mod)


class TestAuditChainLockV238(unittest.TestCase):
    """audit_log chain-fork 修复（finding C 第三件）。"""

    def test_concurrent_audits_chain_intact(self):
        """10 并发 audit() → verify_chain ok（修复前会 fork 假 tamper）。"""
        tmpdir = tempfile.mkdtemp()
        try:
            home = os.path.join(tmpdir, "home")
            os.makedirs(os.path.join(home, ".kb"), exist_ok=True)
            env = os.environ.copy()
            env["HOME"] = home
            code = ("import sys; sys.path.insert(0, %r); "
                    "from audit_log import audit; "
                    "audit('t', 'add', 'x', 'concurrent')" % REPO)
            procs = [subprocess.Popen([sys.executable, "-c", code], env=env,
                                      stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
                     for _ in range(10)]
            for p in procs:
                p.wait()
            vcode = ("import sys, json; sys.path.insert(0, %r); "
                     "from audit_log import verify_chain; "
                     "print(json.dumps(verify_chain()))" % REPO)
            out = subprocess.run([sys.executable, "-c", vcode], env=env,
                                 capture_output=True, text=True)
            r = json.loads(out.stdout)
            self.assertTrue(r.get("ok"), "audit 链 fork: %s" % r)
            self.assertEqual(r.get("total"), 10)
            self.assertEqual(len(r.get("errors", [])), 0)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_audit_lock_source_guard(self):
        with open(os.path.join(REPO, "audit_log.py")) as f:
            c = f.read()
        body = c.split("def audit(")[1].split("\ndef ")[0]
        self.assertIn('AUDIT_FILE + ".lock"', body)
        # 断言**获取**调用（LOCK_EX|LOCK_NB）而非泛 "fcntl.flock"——finally 块的
        # LOCK_UN 释放调用会让泛断言在获取被瘫痪时仍通过（sabotage 实测抓出的弱守卫）。
        self.assertIn("fcntl.LOCK_EX | fcntl.LOCK_NB", body,
                      "audit() 锁获取调用缺失（守卫需匹配获取而非释放）")
        self.assertIn("V37.9.238", body)
        # FAIL-OPEN：锁失败不得阻塞 audit 写入
        self.assertIn("FAIL-OPEN", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
