#!/usr/bin/env python3
"""V37.9.335 — Windows E 盘每日镜像脚本守卫（windows/sync_from_macmini.sh）

背景: 用户要求把 Mac Mini 上本项目生成的全部数据每日 05:00 镜像到 Windows E 盘。
交付 = Windows 侧 WSL+rsync 拉取脚本 + 任务计划 runbook（Mac 端零改动）。

守卫要点:
  1. MR-8 跨文件契约 — 脚本里的 Mac 生产路径不 hardcode 断言，而是从生产脚本源码
     提取比对（openclaw_backup.sh BACKUP_DIR / proxy_filters MEDIA_DIR+STATS_FILE），
     任一侧改路径立即红。
  2. 防灾难镜像 — 每个 --delete 必须配 --max-delete 保险丝（V37.9.324 血案教训:
     "每日重设基线会把灾难吸收进基线"的镜像版: Mac 端事故性清空不得被复制到 E 盘）。
  3. rc=24（活系统文件传输中消失）容忍 + rc=25（保险丝触发）显式告警。
  4. 刻意不拉清单 — 代码仓库 / MOVESPEED KB 副本（一物一形: 拉原件不拉复制品）。
  5. 行为级 — 无可达 host 时诚实失败（exit 4 + last_sync.json ok:false），
     不产生半截成功假象。
  6. 中继带宽适配（V37.9.335-relay/daily）— 办公室封对外 UDP → ZeroTier 仅 TCP 中继；
     ssd_backup 每日只拉**最新一份**（单大文件实测 ~22min/1GB，2026-08-31 用户决策
     恢复每日；周日门控与 FORCE 开关退役）+ /tmp 单实例锁（备份拉取数十分钟，
     防与 05:00 定时重叠）。
  7. 归档全量保留（用户决策 2026-08-30）— E 盘 movespeed_backup 不做任何本地剪枝/删除，
     全部历史档长期保留；单文件拉取形态即保留的结构保证（整目录 --delete 镜像会把
     超出 Mac 7 天轮转窗口的历史档删掉）。
  8. 衍生物排除（V37.9.335-churn，首个 05:00 定时运行实证）— dreams/.map_cache 日期
     前缀缓存每天生灭 ~2400 文件 + text_index/mm_index 每晚重写的向量索引，均可再生
     零归档价值；不排除则 kb 每天撞删除保险丝（2144 待删除实录）。路径从生产者源码
     提取（MR-8）。
"""

import json
import os
import re
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(REPO, "windows", "sync_from_macmini.sh")


def _read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


def _executable_lines(text):
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _module_calls(src):
    """提取全部 run_rsync 调用行: [(name, maxdel, source_path), ...]"""
    calls = []
    for m in re.finditer(r"^run_rsync\s+(\S+)\s+(\d+)\s+'([^']+)'", src, re.MULTILINE):
        calls.append((m.group(1), int(m.group(2)), m.group(3)))
    return calls


class TestCrossFilePathContracts(unittest.TestCase):
    """MR-8: Windows 脚本的 Mac 生产路径与生产脚本源码逐字对齐。"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read("windows/sync_from_macmini.sh")
        cls.calls = _module_calls(cls.src)
        assert len(cls.calls) >= 6, f"防空转: run_rsync 调用提取异常 ({len(cls.calls)})"

    def test_backup_path_matches_openclaw_backup(self):
        """ssd_backup 最新归档探测路径 == openclaw_backup.sh 的 BACKUP_DIR（生产端改路径守卫红）。

        V37.9.335-relay 演进: 中继带宽下 ssd_backup 从 run_rsync 整目录字面调用改为
        `ssh ls -t <dir>/*.tar.gz` 探测最新一份 → 契约锚点随之移到探测行，意图不变。"""
        m = re.search(r'^BACKUP_DIR="([^"]+)"', _read("openclaw_backup.sh"), re.MULTILINE)
        self.assertIsNotNone(m, "openclaw_backup.sh BACKUP_DIR 未找到")
        prod = m.group(1)
        mm = re.search(r"ls -t (/[^\s']+)/\*\.tar\.gz", self.src)
        self.assertIsNotNone(mm, "ssd_backup 最新归档探测行（ls -t <绝对路径>/*.tar.gz）未找到")
        self.assertEqual(mm.group(1).rstrip("/"), prod.rstrip("/"),
                         "备份归档源路径与生产 BACKUP_DIR 漂移")
        self.assertIn('run_rsync ssd_backup 10 "$LATEST_BACKUP"', self.src,
                      "ssd_backup 必须经 run_rsync 拉取探测到的最新归档")

    def test_media_path_covers_proxy_media_dir(self):
        """media 源路径必须覆盖 proxy_filters.MEDIA_DIR（图片注入的存储目录）。"""
        m = re.search(r'MEDIA_DIR = os\.path\.expanduser\("~/([^"]+)"\)',
                      _read("proxy_filters.py"))
        self.assertIsNotNone(m, "proxy_filters MEDIA_DIR 未找到")
        prod_rel = m.group(1)  # e.g. .openclaw/media/inbound
        srcs = {name: path for name, _, path in self.calls}
        self.assertIn("media", srcs)
        self.assertTrue(prod_rel.startswith(srcs["media"].rstrip("/")),
                        f"media 拉取范围 {srcs['media']} 不再覆盖生产 MEDIA_DIR ~/{prod_rel}")

    def test_proxy_stats_filename_matches(self):
        """home_state include 的 proxy_stats 文件名 == proxy_filters.STATS_FILE basename。"""
        m = re.search(r'STATS_FILE = os\.path\.expanduser\("~/([^"]+)"\)',
                      _read("proxy_filters.py"))
        self.assertIsNotNone(m, "proxy_filters STATS_FILE 未找到")
        self.assertIn(f"--include='{m.group(1)}'", self.src,
                      "proxy stats 文件名与生产 STATS_FILE 漂移")

    def test_kb_and_openclaw_sources_present(self):
        srcs = {name: path for name, _, path in self.calls}
        self.assertEqual(srcs.get("kb"), ".kb/")
        self.assertEqual(srcs.get("openclaw_logs"), ".openclaw/logs/")
        self.assertEqual(srcs.get("job_caches"), ".openclaw/jobs/")

    def test_hosts_cover_both_known_ips(self):
        """CLAUDE.md 远程连接节的两个入口（办公室内网 + ZeroTier）都在候选里。"""
        self.assertIn("10.102.0.23", self.src)
        self.assertIn("10.120.230.23", self.src)

    def test_deliberate_exclusions(self):
        """一物一形: 不拉代码仓库整目录、不拉 MOVESPEED 上的 ~/.kb 副本。"""
        for name, _, path in self.calls:
            self.assertNotEqual(path.rstrip("/"), "openclaw-model-bridge",
                                "不得拉整个代码仓库（代码的家在 GitHub）")
            self.assertNotEqual(path.rstrip("/"), "/Volumes/MOVESPEED/KB",
                                "不得拉 KB 的 SSD 副本（拉原件 ~/.kb 不拉复制品）")

    def test_kb_excludes_derived_churn_dirs(self):
        """V37.9.335-churn（首个 05:00 定时运行实证）: .kb 内机器衍生物必须排除——
        dreams/.map_cache 日期前缀缓存每天生灭 ~2400 文件（不排除则 kb 每天撞删除保险丝）；
        text_index/mm_index 向量索引每晚重写且可 --reindex 再生（中继+drvfs 下校验易翻车）。
        三个路径均从生产者源码提取（MR-8: 生产端改缓存/索引位置时本守卫先红）。"""
        dream_src = _read("kb_dream.sh")
        m_dir = re.search(r'^MAP_DIR="\$DREAM_DIR/([^"]+)"', dream_src, re.MULTILINE)
        m_dream = re.search(r'^DREAM_DIR="\$KB_BASE/([^"]+)"', dream_src, re.MULTILINE)
        self.assertIsNotNone(m_dir, "kb_dream.sh MAP_DIR 未找到")
        self.assertIsNotNone(m_dream, "kb_dream.sh DREAM_DIR 未找到")
        map_cache_rel = f"{m_dream.group(1)}/{m_dir.group(1)}"
        m_ti = re.search(r'INDEX_DIR = os\.path\.join\(KB_DIR, "([^"]+)"\)',
                         _read("kb_embed.py"))
        self.assertIsNotNone(m_ti, "kb_embed.py INDEX_DIR 未找到")
        m_mm = re.search(r'INDEX_DIR = os\.path\.expanduser\("~/\.kb/([^"]+)"\)',
                         _read("mm_index.py"))
        self.assertIsNotNone(m_mm, "mm_index.py INDEX_DIR 未找到")
        for rel in (map_cache_rel, m_ti.group(1), m_mm.group(1)):
            self.assertIn(f"--exclude='/{rel.rstrip('/')}/'", self.src,
                          f"kb 模块缺少衍生物排除 /{rel}/（会重演 2144 待删除撞保险丝血案）")


class TestDisasterMirrorFuse(unittest.TestCase):
    """V37.9.324 血案教训的镜像版: --delete 必须配 --max-delete 保险丝。"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read("windows/sync_from_macmini.sh")
        cls.code = _executable_lines(cls.src)

    def test_delete_always_fused(self):
        """rsync 命令行里 --delete 与 --max-delete 必须同现（唯一 rsync 站点）。"""
        rsync_lines = [ln for ln in self.code.splitlines() if "--delete" in ln]
        self.assertTrue(rsync_lines, "防空转: 未找到 --delete 行")
        for ln in rsync_lines:
            self.assertIn("--max-delete", ln,
                          f"--delete 未配 --max-delete 保险丝: {ln.strip()}")

    def test_every_module_passes_numeric_fuse(self):
        calls = _module_calls(self.src)
        self.assertGreaterEqual(len(calls), 6)
        for name, maxdel, _ in calls:
            self.assertGreater(maxdel, 0, f"[{name}] 保险丝必须为正整数")

    def test_drvfs_compatible_rsync_flags(self):
        """V37.9.335-hotfix 血案 pin: E 盘 drvfs 不接受 Unix 权限位/mkstemp(0600)——
        首跑实证 -a 模式所有文件 EPERM 写不进（rc=23 且 du=0）。必须保持 Windows 盘
        兼容标志: --inplace 绕 mkstemp + --no-perms/--no-owner/--no-group/--omit-dir-times，
        且 -t 文件时间戳必须保留（增量判定依据，丢了每天全量重拷）。"""
        rsync_lines = [ln for ln in self.code.splitlines() if "rsync -" in ln and "--" in ln]
        self.assertTrue(rsync_lines, "防空转: 未找到 rsync 调用行")
        joined = " ".join(rsync_lines)
        for flag in ("--inplace", "--no-perms", "--no-owner", "--no-group", "--omit-dir-times"):
            self.assertIn(flag, joined, f"drvfs 兼容标志 {flag} 缺失（回退会重演首跑全量 EPERM）")
        self.assertIn("rsync -rtz", joined, "-rtz 必须保留（-t = 增量判定依据）")
        self.assertNotIn("rsync -az", joined, "-a 含权限/属主/符号链接语义, drvfs 上必炸")

    def test_rc24_tolerated_rc25_alerts(self):
        """rc=24 活文件消失容忍; rc=25 保险丝触发必须显式告警且计入 FAILED。"""
        self.assertIn('"$rc" -eq 24', self.code)
        self.assertIn('"$rc" -eq 25', self.code)
        m25 = re.search(r'-eq 25.*?fi', self.code, re.DOTALL)
        self.assertIsNotNone(m25)
        self.assertIn("FAILED=", m25.group(0), "rc=25 必须计入失败聚合（不得静默）")


class TestRelayBandwidthAdaptation(unittest.TestCase):
    """V37.9.335-relay: 办公室封对外 UDP → ZeroTier 仅 TCP 中继 ~36KB/s 的带宽适配。"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read("windows/sync_from_macmini.sh")
        cls.code = _executable_lines(cls.src)

    def test_ssd_backup_daily_latest_only(self):
        """备份模块 = 每日拉最新一份（用户决策 2026-08-31，22min/1GB 实测支撑）。
        双向禁止回退: (a) 不得恢复周日门控/FORCE 开关（备份 RPO 退回 7 天）
        (b) 不得回退为整目录镜像（--delete 会删掉超出 Mac 7 天轮转的历史档 =
        违背全量保留契约）。"""
        self.assertNotIn("date +%u", self.code, "周日门控已退役（每日拉取），不得回归")
        self.assertNotIn("OPENCLAW_SYNC_FORCE_BACKUP", self.code,
                         "FORCE 开关随每日化退役，不得回归")
        self.assertIn("LATEST_BACKUP", self.code, "最新归档探测必须保留")
        self.assertNotIn("run_rsync ssd_backup     10 '/Volumes", self.code,
                         "ssd_backup 不得回退为整目录拉取"
                         "（--delete 删历史档，违背全量保留）")

    def test_ssd_backup_retains_all_archives_no_prune(self):
        """归档全量保留（用户决策 2026-08-30）: 不得对 E 盘 movespeed_backup 做任何
        本地剪枝/删除——首版的保留 3 份剪枝已按所有者要求退役（~+52GB/年由其接受）。"""
        for ln in self.code.splitlines():
            if "movespeed_backup" in ln:
                self.assertNotRegex(ln, r"\brm\b|xargs|tail -n \+",
                                    f"movespeed_backup 出现删除/剪枝形态: {ln.strip()}")
        self.assertNotIn("tail -n +4", self.code, "保留 3 份剪枝不得回归（全量保留契约）")

    def test_single_instance_lock_in_wsl_native_tmp(self):
        """单实例锁: 每日备份拉取数十分钟（网络差时更久），手动运行与 05:00 定时
        任务不得重叠互踩。锁必须在 WSL 原生 /tmp —— drvfs (/mnt/e) 上 flock 语义不可靠。"""
        self.assertIn("flock -n 9", self.code)
        self.assertRegex(self.code, r"exec 9>/tmp/\S*lock",
                         "锁文件必须放 WSL 原生 /tmp 而非 drvfs")


class TestScriptBehaviorAndShape(unittest.TestCase):

    def test_bash_syntax(self):
        r = subprocess.run(["bash", "-n", SCRIPT], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_no_reachable_host_honest_failure(self):
        """行为级: 无可达 host → exit 4 + last_sync.json ok:false（不产生半截成功假象）。

        fakebin stub rsync（dev 容器无 rsync, 依赖检查会先于探测 exit 3）+ stub ssh
        （exit 255 = 不可达）→ dev/Mac Mini 双环境 hermetic 零真实网络（V37.9.334 惯例）。"""
        tmp = tempfile.mkdtemp(prefix="ws335_")
        fakebin = os.path.join(tmp, "bin")
        os.makedirs(fakebin)
        for name, body in (("rsync", "#!/bin/bash\nexit 0\n"),
                           ("ssh", "#!/bin/bash\nexit 255\n")):
            p = os.path.join(fakebin, name)
            with open(p, "w") as f:
                f.write(body)
            os.chmod(p, 0o755)
        env = os.environ.copy()
        env["OPENCLAW_SYNC_HOSTS"] = "nobody@127.0.0.2"
        env["OPENCLAW_SYNC_DEST"] = tmp
        env["PATH"] = fakebin + os.pathsep + env.get("PATH", "")
        r = subprocess.run(["bash", SCRIPT], capture_output=True, text=True,
                           env=env, timeout=60)
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("无可达 Mac Mini host", r.stdout)
        with open(os.path.join(tmp, "_sync", "last_sync.json"), encoding="utf-8") as f:
            status = json.load(f)
        self.assertFalse(status["ok"])
        self.assertEqual(status["failed"], "no_reachable_host")
        self.assertTrue(os.path.isfile(os.path.join(tmp, "_sync", "sync.log")))

    def test_success_marker_writes_back_to_mac_kb(self):
        """成功时回写 Mac ~/.kb/last_windows_sync.json（time 键 = V37.9.287 契约惯例）。"""
        code = _executable_lines(_read("windows/sync_from_macmini.sh"))
        self.assertIn("last_windows_sync.json", code)
        self.assertIn('\\"time\\"', code)

    def test_home_state_filter_top_level_only(self):
        """home_state 模块必须以 --exclude='*' 封底（只拉顶层清单，不递归进其他目录）。"""
        src = _read("windows/sync_from_macmini.sh")
        m = re.search(r"run_rsync home_state.*?--exclude='\*'", src, re.DOTALL)
        self.assertIsNotNone(m, "home_state 缺 --exclude='*' 封底")

    def test_batchmode_ssh_no_hang(self):
        """无人值守: ssh 必须 BatchMode=yes（凌晨任务绝不挂在密码提示上）。"""
        self.assertIn("BatchMode=yes", _read("windows/sync_from_macmini.sh"))

    def test_v37_9_335_marker(self):
        self.assertIn("V37.9.335", _read("windows/sync_from_macmini.sh"))
        self.assertIn("V37.9.335", _read("windows/README.md"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
