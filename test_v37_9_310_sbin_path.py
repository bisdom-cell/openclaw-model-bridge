#!/usr/bin/env python3
"""V37.9.310 (2026-08-17) — 🔴 备份中断真因: 脚本 PATH 缺 /sbin 致 macOS `mount` 找不到。

血案时间线:
  2026-08-14  V37.9.304 C7 把 SSD 挂载检测由 `-d` 改为 `-d` 且 `mount | grep " on
              /Volumes/MOVESPEED ("`。dev(Linux) 全绿。
  2026-08-15  Mac Mini 同步后第一次备份 → "SSD not mounted" → 此后连续 3 天静默跳过。
  2026-08-17  用户 `mount | grep -i movespeed` 实测: `/dev/disk5s1 on /Volumes/MOVESPEED
              (apfs, ...)` —— SSD 挂载完全正常, 模式也匹配得上该行。

真因不在模式, 在 PATH: **macOS 的 mount 在 `/sbin/mount`**, 而两个 job 脚本 export 的
PATH 是 `/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin` —— **不含
/sbin** → 脚本内 `mount` command-not-found → 管道失败 → `! mount | grep -q` 恒为真 →
恒判"未挂载"。用户交互式 shell 的默认 PATH 含 /sbin, 所以手敲能通、cron 里不通。

为什么 dev 测试全绿: **Linux 的 mount 在 /usr/bin**, 落在旧 PATH 之内 → 同一份代码在
dev 找得到工具、在 macOS 找不到 = 教科书级 dev-production 接缝。我在 V37.9.309 用真实
macOS mount 输出格式做过模拟, 三种文件系统全部判定正确 —— 但那测的是**模式**, 完全
绕开了"这条命令在生产环境的 PATH 里根本不存在"。

本套件钉死两件事:
  1. 任何调用 mount/diskutil 的脚本, 其有效 PATH 必须能解析到 /sbin (macOS 位置);
  2. 工具缺失必须与"没挂载"可区分 —— 缺工具伪装成否定结果, 正是本次难定性的原因
     (同族: V37.9.288「搜索坏了 ≠ 没搜到」)。
"""

import os
import re
import subprocess
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))

# macOS 上这些工具在 /sbin 或 /usr/sbin, 不在 /usr/bin 或 /bin
_SBIN_ONLY_TOOLS = ("mount", "diskutil")

# 直接调用上述工具的脚本 (grep 确认, 非猜测)
_MOUNT_CALLERS = (
    "openclaw_backup.sh",
    "movespeed_daily_sync.sh",
    "movespeed_incident_capture.sh",
)


def _read(name):
    with open(os.path.join(REPO, name), encoding="utf-8") as fh:
        return fh.read()


def _effective_path(src):
    """取脚本最后一次 export PATH= 的字面值（含就地补全分支则并入）。"""
    paths = re.findall(r'export PATH="([^"]+)"', src)
    return paths[-1] if paths else None


def _resolves_sbin(src):
    """脚本的 PATH 是否真的含 `/sbin`（或 `/usr/sbin`）这一**路径分量**。

    🔴 必须按 ":" 切分量精确比对, 不能用 `"/sbin" in path` 子串判断 ——
    `/opt/homebrew/sbin` 里就含子串 "/sbin", 会让守卫恒真。初版即中招:
    sabotage 把 PATH 退回缺 /sbin 时守卫仍全绿, 等于没守。
    """
    for raw in re.findall(r'export PATH="([^"]+)"', src):
        comps = [c for c in raw.split(":") if c]
        if "/sbin" in comps or "/usr/sbin" in comps:
            return True
    return False


def _strip_comments(src):
    """去掉整行注释 —— 注释里写着 `mount | grep ...` 之类的说明文字，
    不去掉会把纯讲解的脚本误判成调用方。"""
    return "\n".join(
        ln for ln in src.splitlines() if not ln.lstrip().startswith("#")
    )


def _calls_sbin_tool(src):
    """脚本是否把 mount/diskutil 当命令调用。

    不能只找 `^mount` 或 `| mount` —— 真实写法是 `! mount | grep`、
    `$(mount | grep ...)`、`mount 2>/dev/null | grep`，工具名既不在行首也不在管道右侧。

    去注释**并去引号字面量**后再按词边界找: full_regression.sh 的 run_suite 描述串里
    含 "mount 模式…" 这类文字, 不去引号会把测试运行器误判成 mount 调用方（初版即中招）。
    真实调用中的 `mount` 一定在引号**之外**（`mount | grep -q " on ... ("`），故不误删。
    """
    body = _strip_comments(src)
    body = re.sub(r'"[^"\n]*"', " ", body)
    body = re.sub(r"'[^'\n]*'", " ", body)
    return any(re.search(rf"\b{t}\b", body) for t in _SBIN_ONLY_TOOLS)


class TestSbinInPath(unittest.TestCase):
    """核心血案回归: 调 mount 的脚本必须能在 macOS 上找到它。"""

    def test_mount_callers_can_resolve_sbin(self):
        for name in _MOUNT_CALLERS:
            with self.subTest(script=name):
                src = _read(name)
                self.assertTrue(
                    _resolves_sbin(src),
                    f"{name} 的 PATH 无 /sbin 分量 → macOS 的 mount(/sbin/mount) "
                    f"command-not-found → 挂载检测恒判未挂载 (2026-08-15 血案)",
                )

    def test_grep_finds_no_unguarded_mount_caller(self):
        """扫全仓: 任何新脚本调 mount/diskutil 都必须带 /sbin（防未来重演）。"""
        offenders = []
        for fn in sorted(os.listdir(REPO)):
            if not fn.endswith(".sh"):
                continue
            src = _read(fn)
            if not _calls_sbin_tool(src):
                continue
            if not _resolves_sbin(src):
                offenders.append(fn)
        self.assertEqual(offenders, [], f"这些脚本调 mount/diskutil 但 PATH 缺 /sbin: {offenders}")

    def test_homebrew_sbin_does_not_satisfy_guard(self):
        """🔴 守卫自身的空转陷阱: /opt/homebrew/sbin 含子串 "/sbin" 但不是 /sbin。

        初版守卫用 `"/sbin" in path` 判断, 被 homebrew 路径满足 → sabotage 退回缺
        /sbin 的 PATH 时守卫仍全绿。此测试钉死必须按分量比对。
        """
        homebrew_only = 'export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/bin:/bin"'
        self.assertFalse(
            _resolves_sbin(homebrew_only),
            "/opt/homebrew/sbin 不得被当成 /sbin (子串陷阱)",
        )
        self.assertTrue(_resolves_sbin('export PATH="/usr/bin:/bin:/sbin"'))
        self.assertTrue(_resolves_sbin('export PATH="$PATH:/sbin:/usr/sbin"'))

    def test_scan_is_not_vacuous(self):
        """防空转: 扫描必须真的认出已知的 mount 调用方。"""
        found = [
            fn for fn in os.listdir(REPO)
            if fn.endswith(".sh") and _calls_sbin_tool(_read(fn))
        ]
        for name in _MOUNT_CALLERS:
            self.assertIn(name, found, f"扫描没认出已知调用方 {name} → 正则失效")


class TestMissingToolIsDistinguishable(unittest.TestCase):
    """缺工具不得伪装成"没挂载"（本次难定性的直接原因）。"""

    def test_backup_has_explicit_mount_availability_check(self):
        src = _read("openclaw_backup.sh")
        self.assertIn("command -v mount", src)
        self.assertIn("mount 命令不可用", src)

    def test_sync_has_explicit_mount_availability_check(self):
        src = _read("movespeed_daily_sync.sh")
        self.assertIn("command -v mount", src)
        self.assertIn("mount 命令不可用", src)

    def test_availability_check_precedes_mount_use(self):
        """可用性检查必须在真正用 mount 之前，否则形同虚设。"""
        for name in ("openclaw_backup.sh", "movespeed_daily_sync.sh"):
            with self.subTest(script=name):
                src = _read(name)
                self.assertLess(
                    src.index("command -v mount"),
                    src.index('mount | grep -q " on /Volumes/MOVESPEED ("'),
                    f"{name}: 可用性检查必须在使用之前",
                )


class TestBehaviorUnderMacOsLikeLayout(unittest.TestCase):
    """行为级: 模拟 macOS 布局（mount 只在 /sbin）验证新旧 PATH 的差异。

    这是**当初该有而没有的那个测试** —— V37.9.309 只测了模式对 mount 输出的匹配,
    没测"这条命令在生产 PATH 下是否存在"。
    """

    def setUp(self):
        import shutil, stat, tempfile
        self.tmp = tempfile.mkdtemp(prefix="v379310_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sbin = os.path.join(self.tmp, "sbin")
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.sbin)
        os.makedirs(self.bin)
        # 严格隔离: 只把 grep 放进 bin —— 绝不引入真实 /usr/bin, 否则 Linux 自带的
        # /usr/bin/mount 会污染"旧 PATH 找不到 mount"这个前提 (初版即中招)。
        real_grep = shutil.which("grep")
        self.assertIsNotNone(real_grep, "需要 grep")
        os.symlink(real_grep, os.path.join(self.bin, "grep"))
        # mount 只在 sbin (macOS 布局)
        p = os.path.join(self.sbin, "mount")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\necho '/dev/disk5s1 on /Volumes/MOVESPEED (apfs, local)'\n")
        os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    def _probe(self, path):
        """在给定 PATH 下跑挂载判据，返回是否判为已挂载。"""
        r = subprocess.run(
            ["sh", "-c",
             'if ! command -v mount >/dev/null 2>&1; then echo TOOL_MISSING; exit 2; fi; '
             'if mount | grep -q " on /Volumes/MOVESPEED ("; then echo MOUNTED; else echo NOT_MOUNTED; fi'],
            capture_output=True, text=True,
            env={"PATH": path}, executable="/bin/sh",
        )
        return r.stdout.strip()

    def test_old_path_misreports_mounted_ssd(self):
        """🔴 血案复现: 旧 PATH（无 /sbin）下, 挂载正常的 SSD 被判为未挂载。"""
        old = self.bin
        self.assertEqual(
            self._probe(old), "TOOL_MISSING",
            "旧 PATH 下应暴露为工具缺失（此前它被当成 NOT_MOUNTED 静默跳过备份）",
        )

    def test_new_path_correctly_detects_mounted_ssd(self):
        """新 PATH（含 /sbin）下, 同一块已挂载 SSD 被正确识别。"""
        new = f"{self.bin}:{self.sbin}"
        self.assertEqual(self._probe(new), "MOUNTED")


class TestShellSyntax(unittest.TestCase):
    def test_bash_syntax_valid(self):
        for name in _MOUNT_CALLERS:
            with self.subTest(script=name):
                r = subprocess.run(
                    ["bash", "-n", os.path.join(REPO, name)],
                    capture_output=True, text=True,
                )
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_marker(self):
        for name in _MOUNT_CALLERS:
            with self.subTest(script=name):
                self.assertIn("V37.9.310", _read(name))


if __name__ == "__main__":
    unittest.main(verbosity=2)
