#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V37.9.226 守卫 — 审计驱动安全加固（SEC-1 data_clean 路径限制 + SEC-2/3 ingest cap）。

背景（多镜头对抗审计发现，2026-07-02）：
  SEC-1 (HIGH): `data_clean` 自定义工具被注入到**每个** tool-enabled 请求（CUSTOM_TOOLS），
    PA LLM 可用任意 `file` 参数调用它 → `os.path.expanduser` → data_clean.py profile/execute
    读文件内容返回给用户。**无路径限制**——untrusted RSS/HN 内容在 PA 上下文（提示注入）
    可诱导它读 ~/.env_shared（API keys）/ ~/.ssh/* / /etc/passwd，解析出的行直接泄漏进 chat。
    修复 = allowlist confinement：resolved realpath（symlink/.. 逃逸归一）必须在允许的数据目录下，
    default-deny + 可见拒绝原因（非静默）+ env 逃生舱 DATA_CLEAN_ALLOWED_DIRS。
  SEC-2/3 (MOD/LOW): tool_proxy/adapter do_POST 的 `int(Content-Length)` 在 try 外裸调
    （畸形值崩线程）+ 无 body 上限（超大 Content-Length 读入前不拒 → OOM）。修复 = 优雅 400
    + 读入前拒超 _MAX_INGEST_BYTES（32MB DoS backstop，legit 流量远低于此）。

测试模式（项目惯例，tool_proxy.py 顶层 serve_forever 无 __main__ guard 不可 import）：
  extract-source + exec-with-mocks 做行为验证 + 源码级守卫防回退 + sabotage 反向验证。
"""
import ast
import os
import re
import unittest

_REPO = os.path.dirname(os.path.abspath(__file__))
_TOOL_PROXY = os.path.join(_REPO, "tool_proxy.py")
_ADAPTER = os.path.join(_REPO, "adapter.py")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _load_confine():
    """抽 _DATA_CLEAN_ALLOWED_ROOTS + 两个 helper 源码，exec 进隔离 namespace。"""
    src = _read(_TOOL_PROXY)
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    chunks = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "_DATA_CLEAN_ALLOWED_ROOTS" for t in node.targets
        ):
            chunks.append("".join(lines[node.lineno - 1:node.end_lineno]))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in (
            "_data_clean_allowed_roots", "_confine_data_path"
        ):
            chunks.append("".join(lines[node.lineno - 1:node.end_lineno]))
    assert len(chunks) == 3, f"期望抽到 const + 2 helper, 实得 {len(chunks)}"
    ns = {"os": os}
    exec("\n".join(chunks), ns)
    return ns


class TestConfinementBehavior(unittest.TestCase):
    """行为级：resolved realpath 必须在 allowlist 内，逃逸/敏感文件被拒。"""

    def setUp(self):
        self._saved = os.environ.pop("DATA_CLEAN_ALLOWED_DIRS", None)
        self.ns = _load_confine()
        self.ns["_DATA_CLEAN_ALLOWED_ROOTS"] = None
        self.confine = self.ns["_confine_data_path"]

    def tearDown(self):
        os.environ.pop("DATA_CLEAN_ALLOWED_DIRS", None)
        if self._saved is not None:
            os.environ["DATA_CLEAN_ALLOWED_DIRS"] = self._saved

    def _ok(self, p):
        return bool(self.confine(p)[0])

    # --- 血案核心: 敏感文件被拒（密钥泄漏防线） ---
    def test_rejects_env_shared(self):
        self.assertFalse(self._ok("~/.env_shared"))

    def test_rejects_etc_passwd(self):
        self.assertFalse(self._ok("/etc/passwd"))

    def test_rejects_ssh_key(self):
        self.assertFalse(self._ok("~/.ssh/id_rsa"))

    def test_rejects_openclaw_dir(self):
        self.assertFalse(self._ok("~/.openclaw/openclaw.json"))

    # --- .. 逃逸 + symlink（realpath 归一）+ prefix 混淆 ---
    def test_rejects_dotdot_escape(self):
        self.assertFalse(self._ok("~/.openclaw/media/inbound/../../.env_shared"))

    def test_rejects_prefix_confusion(self):
        # /x/.data_clean_evil 不得因 startswith /x/.data_clean 而通过
        self.assertFalse(self._ok("~/.data_clean_evil/x"))

    # --- 合法数据目录通过（不破坏功能） ---
    def test_allows_media_inbound(self):
        self.assertTrue(self._ok("~/.openclaw/media/inbound/data.csv"))

    def test_allows_data_clean_workspace(self):
        self.assertTrue(self._ok("~/.data_clean/workspace/x.csv"))

    def test_allows_root_itself(self):
        # 精确等于 root（== root 分支，非 startswith root+sep）
        self.assertTrue(self._ok("~/.data_clean"))

    # --- 空/非法输入 FAIL-CLOSE ---
    def test_rejects_empty(self):
        self.assertFalse(self._ok(""))

    def test_rejects_none(self):
        self.assertFalse(self._ok(None))

    def test_rejects_non_string(self):
        self.assertFalse(self._ok(123))

    # --- env 逃生舱 ---
    def test_env_override_extends_allowlist(self):
        os.environ["DATA_CLEAN_ALLOWED_DIRS"] = "/tmp/mydata"
        self.ns["_DATA_CLEAN_ALLOWED_ROOTS"] = None
        self.assertTrue(self._ok("/tmp/mydata/x.csv"))
        self.assertFalse(self._ok("/tmp/other/x.csv"))

    # --- 返回值契约: ok 时返回 resolved realpath（供 subprocess 用规范路径） ---
    def test_ok_returns_resolved_realpath(self):
        ok, resolved = self.confine("~/.data_clean/workspace/x.csv")
        self.assertTrue(ok)
        self.assertEqual(resolved, os.path.realpath(os.path.expanduser("~/.data_clean/workspace/x.csv")))

    def test_reject_returns_reason_string(self):
        ok, reason = self.confine("~/.env_shared")
        self.assertFalse(ok)
        self.assertIsInstance(reason, str)
        self.assertIn("超出允许", reason)


class TestConfinementWiring(unittest.TestCase):
    """源码级守卫：三个执行点（custom tool profile/execute + REST profile/execute/validate）都过 confine。"""

    def setUp(self):
        self.src = _read(_TOOL_PROXY)

    def test_custom_tool_profile_confined(self):
        # _execute_custom_tool 的 profile/execute 分支不得再直接 expanduser 后无 confine
        m = re.search(r'elif action == "profile":(.*?)elif action == "execute":', self.src, re.S)
        self.assertTrue(m and "_confine_data_path" in m.group(1),
                        "custom tool profile 分支未过 _confine_data_path")

    def test_custom_tool_execute_confined(self):
        m = re.search(r'elif action == "execute":(.*?)else:\n\s+return json\.dumps\(\{"error": f"未知操作',
                      self.src, re.S)
        self.assertTrue(m and "_confine_data_path" in m.group(1),
                        "custom tool execute 分支未过 _confine_data_path")

    def test_rest_shared_file_confined(self):
        # REST _handle_data_clean 的共享 file_path 点用 confine（覆盖 profile/execute/validate original）
        self.assertIn("ok, file_path = _confine_data_path(file_path)", self.src)

    def test_rest_validate_cleaned_confined(self):
        self.assertIn("ok, cleaned = _confine_data_path(cleaned)", self.src)

    def test_no_bare_expanduser_before_subprocess_in_rest(self):
        # 旧 `file_path = os.path.expanduser(file_path)` 直连 subprocess 的形态已退役
        # (REST handler 里 file_path 现只经 confine 赋值)
        self.assertNotIn("        file_path = os.path.expanduser(file_path)\n\n        if not os.path.exists(file_path):",
                         self.src)

    def test_v226_marker(self):
        self.assertIn("V37.9.226", self.src)
        self.assertIn("_confine_data_path", self.src)


class TestIngestCap(unittest.TestCase):
    """源码级守卫：SEC-2/3 — Content-Length int guard + ingest cap 在两个 server 的 do_POST。"""

    def test_proxy_content_length_guarded(self):
        src = _read(_TOOL_PROXY)
        # do_POST 里 int(Content-Length) 现在 try 里 + 有 _MAX_INGEST_BYTES 检查
        m = re.search(r"def do_POST\(self\):.*?raw = self\.rfile\.read\(length\)", src, re.S)
        self.assertTrue(m, "proxy do_POST 结构变了")
        block = m.group(0)
        self.assertIn("except (ValueError, TypeError)", block, "畸形 Content-Length 未 try 保护")
        self.assertIn("_MAX_INGEST_BYTES", block, "无 ingest cap")
        self.assertIn("413", block, "超大 body 未返回 413")

    def test_adapter_content_length_guarded(self):
        src = _read(_ADAPTER)
        m = re.search(r"def do_POST\(self\):.*?raw = self\.rfile\.read\(length\)", src, re.S)
        self.assertTrue(m, "adapter do_POST 结构变了")
        block = m.group(0)
        self.assertIn("except (ValueError, TypeError)", block)
        self.assertIn("_MAX_INGEST_BYTES", block)
        self.assertIn("413", block)

    def test_max_ingest_bytes_defined_both(self):
        self.assertIn("_MAX_INGEST_BYTES = 32", _read(_TOOL_PROXY))
        self.assertIn("_MAX_INGEST_BYTES = 32", _read(_ADAPTER))


if __name__ == "__main__":
    unittest.main()
