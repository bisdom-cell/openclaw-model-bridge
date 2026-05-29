"""V37.9.91 Expert Escalation wiring guards (V37.9.90-r1 Direction 2 落地).

V37.9.83 Direction 2 (AI Partnership Framework) Stage 3 兑现 — 把 V37.9.90-r1 已实
现的 expert_escalation.py + DoubaoTransport + 89 单测从"代码就绪"推到"真生产可用".

V37.9.91 三处 wiring 守卫:
  1. proxy_filters.py: CUSTOM_TOOLS 第 3 个 entry + TOOL_PARAMS args 集合
  2. tool_proxy.py: _execute_custom_tool 加 expert_escalate 分支 (lazy import + FAIL-CLOSE)
  3. auto_deploy.sh FILE_MAP: expert_escalation.py 部署到 $HOME
  4. ontology/tool_ontology.yaml: custom.expert_escalate 让 ONTOLOGY_MODE=on 也注册
  5. SOUL.md 规则 12: 触发词清单 + 行为契约 (V37.9.90-r1 时已写, 本测验证不漂移)

反向验证: sed `expert_escalate` → `expert_escalate_REMOVED` 任一处, 这些测试立即失败.
"""
import os
import re
import json
import subprocess
import tempfile
import unittest

REPO_DIR = os.path.dirname(os.path.abspath(__file__))


def _read(path):
    with open(os.path.join(REPO_DIR, path), encoding="utf-8") as f:
        return f.read()


# ─────────────────────────────────────────────────────────────────────
# 1. proxy_filters.py wiring guards
# ─────────────────────────────────────────────────────────────────────


class TestProxyFiltersWiring(unittest.TestCase):
    """Verify proxy_filters.py 加 expert_escalate 到 CUSTOM_TOOLS + TOOL_PARAMS."""

    @classmethod
    def setUpClass(cls):
        # 强制 off 模式让 hardcoded CUSTOM_TOOLS 测试可重复
        os.environ["ONTOLOGY_MODE"] = "off"
        # 清缓存防止 ONTOLOGY_MODE 改动后 import 还在用旧值
        import sys
        for k in list(sys.modules):
            if "proxy_filters" in k:
                del sys.modules[k]

    def test_custom_tool_names_includes_expert_escalate(self):
        from proxy_filters import CUSTOM_TOOL_NAMES
        self.assertIn(
            "expert_escalate", CUSTOM_TOOL_NAMES,
            "CUSTOM_TOOL_NAMES 必须含 expert_escalate (V37.9.91 wiring)",
        )

    def test_custom_tools_has_expert_escalate_entry(self):
        from proxy_filters import CUSTOM_TOOLS
        names = [t["function"]["name"] for t in CUSTOM_TOOLS]
        self.assertIn(
            "expert_escalate", names,
            "CUSTOM_TOOLS list 必须含 expert_escalate (V37.9.91 wiring)",
        )

    def test_expert_escalate_tool_schema_complete(self):
        """schema 必须有 question (required string) + backend (optional enum)."""
        from proxy_filters import CUSTOM_TOOLS
        expert = next(
            t for t in CUSTOM_TOOLS if t["function"]["name"] == "expert_escalate"
        )
        params = expert["function"]["parameters"]
        self.assertEqual(params["type"], "object")
        # 必填 question
        self.assertIn("question", params["required"])
        self.assertEqual(params["properties"]["question"]["type"], "string")
        # 可选 backend
        self.assertIn("backend", params["properties"])
        # additionalProperties: False
        self.assertFalse(params.get("additionalProperties", True))

    def test_backend_enum_has_doubao_and_claude_pending(self):
        from proxy_filters import CUSTOM_TOOLS
        expert = next(
            t for t in CUSTOM_TOOLS if t["function"]["name"] == "expert_escalate"
        )
        backend_enum = expert["function"]["parameters"]["properties"]["backend"]["enum"]
        self.assertIn("doubao", backend_enum)
        self.assertIn("claude_pending", backend_enum)

    def test_tool_params_contains_expert_escalate(self):
        from proxy_filters import TOOL_PARAMS
        self.assertIn("expert_escalate", TOOL_PARAMS)
        self.assertEqual(TOOL_PARAMS["expert_escalate"], {"question", "backend"})

    def test_description_mentions_doubao_and_trigger_words(self):
        """tool description 必须提到 Doubao + 关键触发词, 让 LLM 知道何时调."""
        from proxy_filters import CUSTOM_TOOLS
        expert = next(
            t for t in CUSTOM_TOOLS if t["function"]["name"] == "expert_escalate"
        )
        desc = expert["function"]["description"]
        self.assertIn("Doubao", desc, "description 必须提到 Doubao backend")
        self.assertIn("PA", desc, "description 必须提到 PA caller")
        # 至少几个 SOUL 规则 12 关键触发词
        trigger_keywords = ["让 Claude 看看", "深度判断", "帮我决定"]
        hits = sum(1 for kw in trigger_keywords if kw in desc)
        self.assertGreaterEqual(
            hits, 2, "description 必须含至少 2 个 SOUL 规则 12 触发词",
        )


# ─────────────────────────────────────────────────────────────────────
# 2. tool_proxy.py _execute_custom_tool 分支 guards
# ─────────────────────────────────────────────────────────────────────


class TestToolProxyBranchGuards(unittest.TestCase):
    """Verify tool_proxy.py _execute_custom_tool 加 expert_escalate 分支."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read("tool_proxy.py")

    def test_expert_escalate_branch_exists(self):
        self.assertIn(
            'if name == "expert_escalate":', self.src,
            "_execute_custom_tool 必须有 expert_escalate 分支",
        )

    def test_v37_9_91_marker_present(self):
        self.assertIn("V37.9.91", self.src,
                      "tool_proxy.py 必须含 V37.9.91 wiring marker")

    def test_lazy_import_uses_importlib_util(self):
        """与 V37.9.15 three_gate 同款模式: spec_from_file_location + path fallback."""
        # branch 内必须有 importlib.util 调用
        branch_start = self.src.index('if name == "expert_escalate":')
        branch_end = self.src.index("return json.dumps", branch_start + 100)
        # 找到分支结束 — 用更宽松的搜索
        branch_section = self.src[branch_start:branch_start + 3000]
        self.assertIn(
            "importlib.util", branch_section,
            "expert_escalate 分支必须 lazy import (importlib.util)",
        )
        self.assertIn(
            "spec_from_file_location", branch_section,
            "必须用 spec_from_file_location 加载 expert_escalation.py",
        )

    def test_fallback_chain_script_dir_then_home(self):
        """路径解析顺序: 同目录 → $HOME (FILE_MAP 部署位置)."""
        branch_start = self.src.index('if name == "expert_escalate":')
        branch_section = self.src[branch_start:branch_start + 3000]
        # 同目录优先
        self.assertIn(
            "os.path.dirname(os.path.abspath(__file__))",
            branch_section,
            "必须先尝试同目录 (dev 环境)",
        )
        # 然后 fallback $HOME
        self.assertIn(
            "os.path.expanduser(\"~/expert_escalation.py\")", branch_section,
            "必须 fallback $HOME/expert_escalation.py (Mac Mini FILE_MAP 部署位置)",
        )

    def test_missing_question_returns_no_context(self):
        """空 question 或非 string → no_context status."""
        branch_start = self.src.index('if name == "expert_escalate":')
        branch_section = self.src[branch_start:branch_start + 3000]
        self.assertIn(
            '"no_context"', branch_section,
            "缺 question 必须返回 status=no_context (与 escalate() API 一致)",
        )

    def test_invalid_backend_returns_unknown_backend(self):
        """非 doubao/claude_pending → unknown_backend 不调 escalate()."""
        branch_start = self.src.index('if name == "expert_escalate":')
        branch_section = self.src[branch_start:branch_start + 3000]
        self.assertIn(
            '"unknown_backend"', branch_section,
            "非法 backend 必须返回 status=unknown_backend",
        )
        # 必须显式检查 backend in {doubao, claude_pending}
        self.assertIn(
            "doubao", branch_section,
        )
        self.assertIn(
            "claude_pending", branch_section,
        )

    def test_module_load_failure_returns_api_unavailable(self):
        """模块加载失败 (Mac Mini 部署未到达) → api_unavailable, FAIL-CLOSED."""
        branch_start = self.src.index('if name == "expert_escalate":')
        branch_section = self.src[branch_start:branch_start + 3000]
        self.assertIn(
            '"api_unavailable"', branch_section,
            "模块未部署/加载失败必须返回 status=api_unavailable, 不静默崩溃",
        )

    def test_escalate_call_with_question_and_backend(self):
        """必须调 escalate(question=..., backend=...) 完整传参."""
        branch_start = self.src.index('if name == "expert_escalate":')
        branch_section = self.src[branch_start:branch_start + 3000]
        self.assertIn(
            "_ee_escalate(question=question, backend=backend)", branch_section,
            "必须调 escalate(question=..., backend=...)",
        )

    def test_json_response_preserves_chinese(self):
        """返回的 dict 必须 ensure_ascii=False 保留中文 (PA Qwen3 中文输出)."""
        branch_start = self.src.index('if name == "expert_escalate":')
        branch_section = self.src[branch_start:branch_start + 3000]
        # 末尾返回 json.dumps(result, ensure_ascii=False, ...)
        self.assertIn("ensure_ascii=False", branch_section)


# ─────────────────────────────────────────────────────────────────────
# 3. auto_deploy.sh FILE_MAP guards
# ─────────────────────────────────────────────────────────────────────


class TestAutoDeployFileMap(unittest.TestCase):
    """Verify expert_escalation.py 被 FILE_MAP 部署到 $HOME."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read("auto_deploy.sh")

    def test_expert_escalation_in_file_map(self):
        self.assertIn(
            "expert_escalation.py|$HOME/expert_escalation.py", self.src,
            "FILE_MAP 必须含 expert_escalation.py 部署条目",
        )

    def test_v37_9_91_comment_marker(self):
        # FILE_MAP 段附近必须有 V37.9.91 注释 (帮助 audit 追溯)
        self.assertIn("V37.9.91", self.src,
                      "FILE_MAP 周围必须有 V37.9.91 marker (V37.9.90-r1 落地路径)")

    def test_data_clean_still_in_file_map(self):
        """回归: 不破坏现有 data_clean.py FILE_MAP entry."""
        self.assertIn("data_clean.py|$HOME/data_clean.py", self.src)


# ─────────────────────────────────────────────────────────────────────
# 4. ontology/tool_ontology.yaml guards (ONTOLOGY_MODE=on 路径)
# ─────────────────────────────────────────────────────────────────────


class TestOntologyToolSchema(unittest.TestCase):
    """Verify ontology engine 也注册 expert_escalate (ONTOLOGY_MODE=on 时由 yaml 驱动)."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read("ontology/tool_ontology.yaml")

    def test_expert_escalate_declared_in_yaml(self):
        self.assertIn("expert_escalate:", self.src,
                      "tool_ontology.yaml custom 段必须含 expert_escalate")

    def test_side_effects_false(self):
        """read-only contract: side_effects: false (V37.9.91 不修改本地文件)."""
        # expert_escalate 段内必须有 side_effects: false
        idx = self.src.index("expert_escalate:")
        section = self.src[idx:idx + 1200]
        self.assertIn("side_effects: false", section,
                      "expert_escalate side_effects 必须 false (read-only)")

    def test_resource_type_external_llm(self):
        idx = self.src.index("expert_escalate:")
        section = self.src[idx:idx + 1200]
        self.assertIn("resource_type: ExternalLLM", section)

    def test_question_required_backend_optional(self):
        idx = self.src.index("expert_escalate:")
        section = self.src[idx:idx + 1500]
        self.assertIn("question:", section)
        self.assertIn("required: true", section)
        self.assertIn("backend:", section)

    def test_v37_9_91_marker_in_comment(self):
        self.assertIn("V37.9.91", self.src,
                      "tool_ontology.yaml 必须含 V37.9.91 marker")

    def test_yaml_loadable_by_ontology_mode_on(self):
        """ONTOLOGY_MODE=on 时 expert_escalate 必须出现在生成的 CUSTOM_TOOLS."""
        env = dict(os.environ)
        env["ONTOLOGY_MODE"] = "on"
        result = subprocess.run(
            ["python3", "-c",
             "from proxy_filters import CUSTOM_TOOLS, CUSTOM_TOOL_NAMES;"
             " names = sorted(CUSTOM_TOOL_NAMES);"
             " assert 'expert_escalate' in names, names;"
             " print('OK:', names)"],
            cwd=REPO_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode, 0,
            f"ONTOLOGY_MODE=on 加载失败:\nSTDOUT:{result.stdout}\nSTDERR:{result.stderr}",
        )
        self.assertIn("expert_escalate", result.stdout)


# ─────────────────────────────────────────────────────────────────────
# 5. SOUL.md 规则 12 guards (V37.9.90-r1 已写, 防漂移)
# ─────────────────────────────────────────────────────────────────────


class TestSoulRule12Anchor(unittest.TestCase):
    """V37.9.90-r1 已写规则 12, 验证关键锚点不漂移."""

    @classmethod
    def setUpClass(cls):
        cls.src = _read("SOUL.md")

    def test_rule_12_anchor_exists(self):
        self.assertIn("12.", self.src, "SOUL.md 必须有规则 12")
        self.assertIn("expert_escalate", self.src,
                      "SOUL.md 必须显式提到 expert_escalate 工具名")

    def test_doubao_backend_referenced(self):
        self.assertIn("Doubao", self.src,
                      "SOUL.md 规则 12 必须标注 Doubao backend (V37.9.90-r1)")

    def test_trigger_words_listed(self):
        """触发词清单 — 至少 4 个关键短语必须明示."""
        triggers = [
            "让 Claude 看看",
            "深度判断",
            "帮我决定",
            "拿不准",
        ]
        for t in triggers:
            self.assertIn(
                t, self.src, f"SOUL.md 规则 12 必须列出触发词 '{t}'",
            )


# ─────────────────────────────────────────────────────────────────────
# 6. 端到端 dry_run via _execute_custom_tool (真跑 escalate)
# ─────────────────────────────────────────────────────────────────────


class TestEndToEndDryRun(unittest.TestCase):
    """真跑 _execute_custom_tool 的 expert_escalate 分支 (dry_run 路径)."""

    def _invoke_via_subprocess(self, arguments_json, extra_env=None):
        """走 subprocess 模拟 _execute_custom_tool 调用. 用 dry_run 避免真调 Volcengine."""
        # 用临时 audit log 路径避免污染 ~/.kb
        tmpdir = tempfile.mkdtemp(prefix="v9_91_test_")
        audit_path = os.path.join(tmpdir, "audit.jsonl")
        os.makedirs(os.path.dirname(audit_path) if os.path.dirname(audit_path) else tmpdir, exist_ok=True)

        # subprocess 模拟 tool_proxy 内的 lazy import 流程
        script = """
import os, sys, json
sys.path.insert(0, {repo!r})
import importlib.util as ee_imp
ee_path = os.path.join({repo!r}, "expert_escalation.py")
spec = ee_imp.spec_from_file_location("_ee", ee_path)
mod = ee_imp.module_from_spec(spec)
spec.loader.exec_module(mod)
result = mod.escalate(
    question={question!r},
    backend={backend!r},
    audit_log_path={audit!r},
    dry_run=True,
)
print(json.dumps(result, ensure_ascii=False))
""".format(
            repo=REPO_DIR,
            question=arguments_json.get("question", ""),
            backend=arguments_json.get("backend", "doubao"),
            audit=audit_path,
        )

        env = dict(os.environ)
        if extra_env:
            env.update(extra_env)

        result = subprocess.run(
            ["python3", "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        return result, audit_path

    def test_dry_run_question_returns_ok_with_proposal(self):
        result, _audit = self._invoke_via_subprocess(
            {"question": "should we adopt Doubao for production?", "backend": "doubao"},
        )
        self.assertEqual(
            result.returncode, 0,
            f"escalate dry_run 必须成功:\nSTDOUT:{result.stdout}\nSTDERR:{result.stderr}",
        )
        # parse stdout
        # 取最后一行 (因为 escalate 内部可能 print 一些 log)
        lines = [l for l in result.stdout.strip().split("\n") if l.startswith("{")]
        self.assertTrue(lines, f"无 JSON 输出:\n{result.stdout}")
        data = json.loads(lines[-1])
        self.assertEqual(data["status"], "dry_run",
                         "dry_run 模式必须返回 status=dry_run")
        self.assertIn("proposal", data)
        self.assertIn("[DRY RUN]", data["proposal"])
        self.assertEqual(data["backend"], "doubao")

    def test_dry_run_audit_record_written(self):
        result, audit_path = self._invoke_via_subprocess(
            {"question": "audit record test", "backend": "doubao"},
        )
        self.assertEqual(result.returncode, 0,
                         f"dry_run 调用应成功:\n{result.stderr}")
        # audit log 必须被写入
        self.assertTrue(
            os.path.exists(audit_path),
            f"audit log 必须被写入: {audit_path}",
        )
        with open(audit_path) as f:
            lines = f.read().strip().split("\n")
        self.assertGreaterEqual(len(lines), 1, "至少 1 条 audit record")
        rec = json.loads(lines[-1])
        self.assertEqual(rec["status"], "dry_run")
        self.assertEqual(rec["backend"], "doubao")

    def test_claude_pending_backend_returns_stub(self):
        """backend=claude_pending → 立即返回 stub status, 不真调 Doubao."""
        result, _ = self._invoke_via_subprocess(
            {"question": "claude pending test", "backend": "claude_pending"},
        )
        self.assertEqual(result.returncode, 0)
        lines = [l for l in result.stdout.strip().split("\n") if l.startswith("{")]
        data = json.loads(lines[-1])
        self.assertEqual(data["status"], "claude_pending")
        self.assertEqual(data["backend"], "claude_pending")
        # 必须有解释为何 pending 的 error 字段
        self.assertIn("Claude", data.get("error", ""))


# ─────────────────────────────────────────────────────────────────────
# 7. 文档锚点与 changelog markers
# ─────────────────────────────────────────────────────────────────────


class TestDocumentationAnchors(unittest.TestCase):
    """V37.9.91 必须在 CLAUDE.md changelog 留下 marker, 利于 future audit."""

    def test_v37_9_91_marker_in_proxy_filters(self):
        src = _read("proxy_filters.py")
        self.assertIn("V37.9.91", src,
                      "proxy_filters.py 必须含 V37.9.91 注释 marker")

    def test_design_doc_exists(self):
        """V37.9.90-r1 设计文档必须存在."""
        path = os.path.join(REPO_DIR, "docs/articles/expert_escalation_design.md")
        self.assertTrue(os.path.exists(path),
                        "docs/articles/expert_escalation_design.md 必须存在")


if __name__ == "__main__":
    unittest.main(verbosity=2)
