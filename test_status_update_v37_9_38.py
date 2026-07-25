#!/usr/bin/env python3
"""test_status_update_v37_9_38.py — V37.9.36 候选 / V37.9.38 闭环守卫测试

背景：V37.9.35 收工 `--add unfinished` 把 3 项 todo 写到 **顶层**
``data["unfinished"]``（数组），但 V37.9.36 开工 `--read --human` 显示的是
``data["session_context"]["unfinished"]``，两条路径长期分叉（实测顶层 32 项 +
session_context 13 项 = 45 项漂移），破坏开工读到收工写的闭环（原则 #2/#9）。

V37.9.38 修复策略：
  1. ``_resolve_array_target(data, name)`` 把 ``unfinished`` 重定向到
     ``session_context.unfinished``（schema 真理源），其他数组照常走顶层
  2. ``_migrate_top_level_unfinished(data)`` 在 ``load_status()`` 时把顶层遗留
     列表合并去重进 session_context.unfinished，下一次 save 持久化为单一路径
  3. ``--add / --pop / --clear unfinished`` 三处都走 ``_resolve_array_target``
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


def _load_status_update_module():
    """直接 spec_from_file_location 加载 status_update.py（避开 main 入口）。"""
    spec = importlib.util.spec_from_file_location(
        "status_update_v37938_test",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "status_update.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_su = _load_status_update_module()


class TestResolveArrayTargetSpecialCase(unittest.TestCase):
    """V37.9.38: --add/--pop/--clear 的 unfinished 重定向"""

    def test_unfinished_redirects_to_session_context(self):
        """unfinished 必须走 session_context.unfinished（schema 真理源）"""
        data = {}
        parent, key = _su._resolve_array_target(data, "unfinished")
        self.assertIs(parent, data["session_context"])
        self.assertEqual(key, "unfinished")
        self.assertIsInstance(parent[key], list)

    def test_other_arrays_use_top_level(self):
        """非 unfinished 数组继续走顶层（不改变现有语义）"""
        for name in ("priorities", "feedback", "incidents", "preferences",
                     "operating_rules", "recent_changes"):
            data = {}
            parent, key = _su._resolve_array_target(data, name)
            self.assertIs(parent, data, f"{name} 应走顶层而不是 session_context")
            self.assertEqual(key, name)

    def test_coerces_legacy_string_session_context_unfinished_to_list(self):
        """legacy schema 把 session_context.unfinished 当字符串，需自动转 list"""
        data = {"session_context": {"unfinished": "数据清洗 Phase2 设计中"}}
        parent, key = _su._resolve_array_target(data, "unfinished")
        self.assertIsInstance(parent[key], list)
        self.assertEqual(parent[key], ["数据清洗 Phase2 设计中"])

    def test_coerces_empty_string_to_empty_list(self):
        """空字符串/空白字符串转空 list（不保留 [''] 这种垃圾项）"""
        data = {"session_context": {"unfinished": ""}}
        parent, key = _su._resolve_array_target(data, "unfinished")
        self.assertEqual(parent[key], [])

        data2 = {"session_context": {"unfinished": "   "}}
        parent2, key2 = _su._resolve_array_target(data2, "unfinished")
        self.assertEqual(parent2[key2], [])

    def test_does_not_overwrite_existing_list(self):
        """已是 list 时不应被重置（防止破坏现有数据）"""
        data = {"session_context": {"unfinished": ["item1", "item2"]}}
        parent, key = _su._resolve_array_target(data, "unfinished")
        self.assertEqual(parent[key], ["item1", "item2"])

    def test_creates_session_context_if_missing(self):
        """data 没有 session_context 键也能正常处理"""
        data = {"priorities": []}
        parent, key = _su._resolve_array_target(data, "unfinished")
        self.assertIn("session_context", data)
        self.assertIn("unfinished", data["session_context"])

    def test_fully_qualified_unfinished_redirects_no_orphan(self):
        """V37.9.270 血案回归: `--add session_context.unfinished`（全限定形式）
        必须重定向到 nested，绝不创建字面点号顶层孤儿键 `data["session_context.unfinished"]`
        （curation 静默丢失）。sabotage: _resolve 只特例化裸名 → 全限定 fall through → 孤儿。"""
        data = {"session_context": {"unfinished": ["existing"]}}
        parent, key = _su._resolve_array_target(data, "session_context.unfinished")
        # 重定向到 nested 的真理源
        self.assertIs(parent, data["session_context"])
        self.assertEqual(key, "unfinished")
        # 模拟调用方 append，确认落在 nested 而非孤儿键
        parent[key].append("new_item")
        self.assertEqual(data["session_context"]["unfinished"], ["existing", "new_item"])
        self.assertNotIn("session_context.unfinished", data,
                         "全限定形式绝不能创建字面点号顶层孤儿键")


class TestMigrationFromTopLevel(unittest.TestCase):
    """V37.9.38: load_status 加载时把顶层 unfinished 合并迁移"""

    def test_merges_top_level_into_session_context(self):
        """顶层非空 list → 合并进 session_context，删除顶层"""
        data = {
            "unfinished": ["legacy_a", "legacy_b"],
            "session_context": {"unfinished": ["recent_a"]},
        }
        _su._migrate_top_level_unfinished(data)
        # session_context 在前（更新），顶层在后（更旧）
        self.assertEqual(data["session_context"]["unfinished"],
                         ["recent_a", "legacy_a", "legacy_b"])
        # 顶层应被清理
        self.assertNotIn("unfinished", data)

    def test_dedups_overlapping_items(self):
        """两条路径中重复的字符串项必须只保留一份"""
        data = {
            "unfinished": ["shared", "legacy_only"],
            "session_context": {"unfinished": ["shared", "recent_only"]},
        }
        _su._migrate_top_level_unfinished(data)
        # session_context "shared" 优先保留，顶层 "shared" 被去重
        self.assertEqual(data["session_context"]["unfinished"],
                         ["shared", "recent_only", "legacy_only"])

    def test_dedups_dict_items_by_canonical_json(self):
        """dict 项按 canonical JSON 去重（key 顺序不影响匹配）"""
        data = {
            "unfinished": [{"task": "X", "status": "open"}],
            "session_context": {
                "unfinished": [{"status": "open", "task": "X"}],  # 同内容不同顺序
            },
        }
        _su._migrate_top_level_unfinished(data)
        self.assertEqual(len(data["session_context"]["unfinished"]), 1)
        self.assertNotIn("unfinished", data)

    def test_migration_idempotent(self):
        """二次跑不重复（迁移完顶层已删，第二次进入空分支早返回）"""
        data = {
            "unfinished": ["a", "b"],
            "session_context": {"unfinished": []},
        }
        _su._migrate_top_level_unfinished(data)
        first = list(data["session_context"]["unfinished"])
        _su._migrate_top_level_unfinished(data)
        second = list(data["session_context"]["unfinished"])
        self.assertEqual(first, second)

    def test_does_not_mutate_when_top_level_missing(self):
        """data 中没有顶层 unfinished 时 session_context 完全不动"""
        before = {"session_context": {"unfinished": ["keep_me"]}}
        _su._migrate_top_level_unfinished(before)
        self.assertEqual(before["session_context"]["unfinished"], ["keep_me"])
        self.assertNotIn("unfinished", before)

    def test_removes_empty_top_level_list(self):
        """顶层为空 list（schema 漂移残留）也清理掉"""
        data = {"unfinished": [], "session_context": {"unfinished": ["X"]}}
        _su._migrate_top_level_unfinished(data)
        self.assertNotIn("unfinished", data)
        self.assertEqual(data["session_context"]["unfinished"], ["X"])

    def test_migrates_dotted_orphan_key(self):
        """V37.9.270 血案回归: 字面点号孤儿键 `data["session_context.unfinished"]`
        （--add 全限定形式误建）必须被 drain 进 nested + 删除，无数据丢失。"""
        data = {
            "session_context.unfinished": ["lost_glm_item_a", "lost_glm_item_b"],
            "session_context": {"unfinished": ["visible_item"]},
        }
        _su._migrate_top_level_unfinished(data)
        # nested 优先在前，孤儿恢复在后 —— 丢失的项重新可见
        self.assertEqual(data["session_context"]["unfinished"],
                         ["visible_item", "lost_glm_item_a", "lost_glm_item_b"])
        # 孤儿键被删 —— 不再一物多形
        self.assertNotIn("session_context.unfinished", data)

    def test_drains_both_orphan_sources_together(self):
        """两个孤儿源（裸 unfinished + 点号 session_context.unfinished）同时存在时
        都 drain 进 nested，去重，全删除。"""
        data = {
            "unfinished": ["legacy_top"],
            "session_context.unfinished": ["dotted_orphan", "shared"],
            "session_context": {"unfinished": ["shared", "recent"]},
        }
        _su._migrate_top_level_unfinished(data)
        merged = data["session_context"]["unfinished"]
        # nested 优先 + 两孤儿源附加 + "shared" 去重只留一份
        self.assertEqual(merged, ["shared", "recent", "legacy_top", "dotted_orphan"])
        self.assertNotIn("unfinished", data)
        self.assertNotIn("session_context.unfinished", data)

    def test_removes_empty_dotted_orphan_key(self):
        """点号孤儿键为空 list 也清理（schema 漂移不残留）"""
        data = {"session_context.unfinished": [],
                "session_context": {"unfinished": ["X"]}}
        _su._migrate_top_level_unfinished(data)
        self.assertNotIn("session_context.unfinished", data)
        self.assertEqual(data["session_context"]["unfinished"], ["X"])

    def test_handles_legacy_string_session_context(self):
        """session_context.unfinished 是字符串（DEFAULT_STATUS 历史 schema）"""
        data = {
            "unfinished": ["legacy_list_item"],
            "session_context": {"unfinished": "data clean phase2"},
        }
        _su._migrate_top_level_unfinished(data)
        self.assertEqual(data["session_context"]["unfinished"],
                         ["data clean phase2", "legacy_list_item"])

    def test_no_op_when_top_level_not_a_list(self):
        """顶层是 str/int/dict 等异常类型时不动它（避免破坏未知数据）"""
        data = {"unfinished": "误用为字符串", "session_context": {"unfinished": []}}
        _su._migrate_top_level_unfinished(data)
        # 顶层不是 list 不该删，等用户/Claude Code 显式处理
        self.assertEqual(data["unfinished"], "误用为字符串")


class TestEndToEndAddUnfinishedSubprocess(unittest.TestCase):
    """端到端：subprocess 跑 --add unfinished 并验证 session_context 落盘"""

    def setUp(self):
        """每个测试一份隔离的 HOME + work 目录，仅初始化一次 status.json。"""
        self.tmp = tempfile.mkdtemp()
        self.repo_root = os.path.dirname(os.path.abspath(__file__))
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.work, exist_ok=True)
        # 拷贝 status_update.py 到隔离 work（subprocess 用 cwd=work 跑）
        shutil.copy(os.path.join(self.repo_root, "status_update.py"), self.work)
        # 一次性初始化空 status.json，后续 _run 复用之
        self.status_file = os.path.join(self.work, "status.json")
        with open(self.status_file, "w") as f:
            json.dump({}, f)
        # ~/.kb/status.json 不应该存在（防生产路径污染）
        os.makedirs(os.path.join(self.tmp, ".kb"), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *args):
        """运行 status_update.py CLI，HOME 隔离 + cwd=work 让 fallback 路径命中
        self.status_file（即 work/status.json，不重置不复制，纯复用）。
        """
        env = os.environ.copy()
        env["HOME"] = self.tmp  # ~/.kb/status.json 不存在 → fallback 到 cwd
        return subprocess.run(
            [sys.executable, "status_update.py"] + list(args),
            cwd=self.work, env=env, capture_output=True, text=True, timeout=20,
        )

    def test_add_unfinished_writes_to_session_context(self):
        """--add unfinished X 必须写入 session_context.unfinished 而非顶层"""
        result = self._run("--add", "unfinished", "新任务 X", "--by", "test")
        self.assertEqual(result.returncode, 0, msg=f"stderr={result.stderr}")
        # 读回验证
        with open(os.path.join(self.tmp, "work", "status.json")) as f:
            data = json.load(f)
        self.assertIn("session_context", data)
        self.assertIn("unfinished", data["session_context"])
        self.assertIn("新任务 X", data["session_context"]["unfinished"])
        # 严禁回归到顶层
        self.assertNotIn("unfinished", {k: v for k, v in data.items() if k != "session_context"})

    def test_pop_unfinished_targets_session_context(self):
        """--pop unfinished N 从 session_context.unfinished 移除"""
        # 先 add 三条
        for item in ("A", "B", "C"):
            r = self._run("--add", "unfinished", item, "--by", "test")
            self.assertEqual(r.returncode, 0, msg=r.stderr)
        # pop index 1 应该移除 "B"
        result = self._run("--pop", "unfinished", "1", "--by", "test")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("B", result.stdout)
        with open(os.path.join(self.tmp, "work", "status.json")) as f:
            data = json.load(f)
        remaining = data["session_context"]["unfinished"]
        self.assertEqual(remaining, ["A", "C"])

    def test_clear_unfinished_targets_session_context(self):
        """--clear unfinished 清空 session_context.unfinished"""
        for item in ("X", "Y"):
            self._run("--add", "unfinished", item, "--by", "test")
        result = self._run("--clear", "unfinished", "--by", "test")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        with open(os.path.join(self.tmp, "work", "status.json")) as f:
            data = json.load(f)
        self.assertEqual(data["session_context"]["unfinished"], [])

    def test_blood_lesson_open_close_roundtrip(self):
        """V37.9.36 血案场景复现：收工 add 的 todo 开工必能 read 到"""
        # 收工 add 3 项
        for item in ("todo_1", "todo_2", "todo_3"):
            r = self._run("--add", "unfinished", item, "--by", "claude_code")
            self.assertEqual(r.returncode, 0, msg=r.stderr)
        # 开工 read --human 必须包含全部 3 项
        result = self._run("--read", "--human")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        for item in ("todo_1", "todo_2", "todo_3"):
            self.assertIn(item, result.stdout, msg=f"血案回归: {item} 没在 --read --human 显示")
        # 计数也必须正确
        self.assertIn("未完成 (3 项)", result.stdout)


class TestSourceLevelGuards(unittest.TestCase):
    """源码级守卫：防止未来重构回退反模式"""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "status_update.py")) as f:
            cls.src = f.read()

    def test_resolve_array_target_function_defined(self):
        self.assertIn("def _resolve_array_target(data, array_name)", self.src)

    def test_migrate_function_defined(self):
        self.assertIn("def _migrate_top_level_unfinished(data)", self.src)

    def test_load_status_invokes_migration(self):
        """load_status() 必须调用迁移 helper（in-memory 修复 V37.9.36 漂移）"""
        # 提取 load_status 函数体（粗略：从 def load_status 到下个 def）
        idx = self.src.find("def load_status()")
        self.assertGreater(idx, 0, "找不到 load_status()")
        end = self.src.find("\ndef ", idx + 1)
        body = self.src[idx:end] if end > 0 else self.src[idx:]
        self.assertIn("_migrate_top_level_unfinished(data)", body)

    def test_args_add_uses_resolve_target(self):
        """args.add 分支必须用 _resolve_array_target，不再直接读 data[array_name]"""
        # 找到 args.add 块
        idx = self.src.find("if args.add:")
        self.assertGreater(idx, 0)
        # 取 args.add 后到下一个顶层 if 之前的代码块
        end = self.src.find("\n    if args.pop:", idx)
        self.assertGreater(end, 0)
        body = self.src[idx:end]
        self.assertIn("_resolve_array_target(data, array_name)", body)
        # 反模式：禁止直接 data[array_name].append
        self.assertNotIn("data[array_name].append", body,
                         msg="V37.9.38 回归: --add 必须走 _resolve_array_target")
        self.assertNotIn("data[array_name].insert", body,
                         msg="V37.9.38 回归: --add 必须走 _resolve_array_target")

    def test_args_pop_uses_resolve_target(self):
        idx = self.src.find("if args.pop:")
        end = self.src.find("\n    if args.clear:", idx)
        body = self.src[idx:end]
        self.assertIn("_resolve_array_target(data, array_name)", body)
        self.assertNotIn("data[array_name].pop(idx)", body,
                         msg="V37.9.38 回归: --pop 必须走 _resolve_array_target")

    def test_args_clear_uses_resolve_target(self):
        idx = self.src.find("if args.clear:")
        end = self.src.find("\n    if args.update_priority:", idx)
        body = self.src[idx:end]
        self.assertIn("_resolve_array_target(data, args.clear)", body)
        self.assertNotIn("data[args.clear] = []", body,
                         msg="V37.9.38 回归: --clear 必须走 _resolve_array_target")

    def test_v37_9_38_marker_present(self):
        """源码必须保留 V37.9.38 标记（追溯审计）"""
        self.assertIn("V37.9.38", self.src)

    def test_format_human_enumerates_list(self):
        """format_human 把 unfinished list 逐项展开（不再用 Python repr 挤一行）"""
        idx = self.src.find("def format_human(data)")
        end = self.src.find("\ndef ", idx + 1)
        body = self.src[idx:end] if end > 0 else self.src[idx:]
        # 必须含逐项展开逻辑
        self.assertIn("isinstance(unfinished, list)", body)
        self.assertIn("for i, item in enumerate(unfinished", body)
        # 必须有截断逻辑（防超长 dict/string 撑爆终端）
        self.assertIn("[:220]", body)


class TestGenericDottedArrayResolutionV275(unittest.TestCase):
    """V37.9.275: 非 unfinished 点号数组名的通用嵌套解析（V37.9.270 残余半边）。

    血案（2026-07-24 当场观察）：`--clear session_context.open_prs` rc=0 但静默
    no-op + 吐 help —— _resolve_array_target 只特例化 unfinished，其他点号名
    fall through 到字面顶层键：--add 造孤儿键（V37.9.270 同机制）/ --pop/--clear
    静默 no-op。修复 = 通用点号 walk + 非 dict 中间节点保守回退（不像 set_nested
    覆盖为 {} 破坏标量）。
    """

    def test_dotted_open_prs_resolves_nested(self):
        """session_context.open_prs 解析到 nested dict，不产生字面点号键"""
        data = {}
        parent, key = _su._resolve_array_target(data, "session_context.open_prs")
        self.assertIs(parent, data["session_context"])
        self.assertEqual(key, "open_prs")
        self.assertNotIn("session_context.open_prs", data)

    def test_dotted_resolution_reaches_existing_list(self):
        """已存在的 nested 列表必须被解析命中（--clear 的血案路径）"""
        data = {"session_context": {"open_prs": ["stale-pr"]}}
        parent, key = _su._resolve_array_target(data, "session_context.open_prs")
        self.assertIs(parent[key], data["session_context"]["open_prs"])

    def test_deep_dotted_path_walks_and_creates(self):
        """多级点号路径逐级创建中间 dict（镜像 set_nested 路径语义）"""
        data = {}
        parent, key = _su._resolve_array_target(data, "a.b.c")
        self.assertIs(parent, data["a"]["b"])
        self.assertEqual(key, "c")

    def test_non_dict_intermediate_conservative_fallback(self):
        """非 dict 中间节点 → 保守回退字面行为，绝不覆盖既有标量（与 set_nested 刻意不同）"""
        data = {"session_context": "corrupted-scalar"}
        parent, key = _su._resolve_array_target(data, "session_context.open_prs")
        self.assertIs(parent, data)
        self.assertEqual(key, "session_context.open_prs")
        self.assertEqual(data["session_context"], "corrupted-scalar")

    def test_unfinished_special_case_untouched(self):
        """unfinished 特例（裸名 + 全限定）优先级不被通用 walk 破坏"""
        for name in ("unfinished", "session_context.unfinished"):
            data = {}
            parent, key = _su._resolve_array_target(data, name)
            self.assertIs(parent, data["session_context"])
            self.assertEqual(key, "unfinished")

    def test_v37_9_275_marker_present(self):
        src_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "status_update.py")
        with open(src_path) as f:
            src = f.read()
        self.assertIn("V37.9.275", src)


class TestEndToEndDottedArrayCliV275(unittest.TestCase):
    """端到端：subprocess 跑 --add/--pop/--clear session_context.open_prs"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo_root = os.path.dirname(os.path.abspath(__file__))
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.work, exist_ok=True)
        shutil.copy(os.path.join(self.repo_root, "status_update.py"), self.work)
        self.status_file = os.path.join(self.work, "status.json")
        with open(self.status_file, "w") as f:
            json.dump({"session_context": {"open_prs": ["stale-pr-entry"]}}, f)
        os.makedirs(os.path.join(self.tmp, ".kb"), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *args):
        env = os.environ.copy()
        env["HOME"] = self.tmp
        return subprocess.run(
            [sys.executable, "status_update.py"] + list(args),
            cwd=self.work, env=env, capture_output=True, text=True, timeout=20,
        )

    def _read(self):
        with open(self.status_file) as f:
            return json.load(f)

    def test_blood_lesson_clear_dotted_open_prs(self):
        """血案回归：--clear session_context.open_prs 必须真清空（原静默 no-op + 吐 help）"""
        result = self._run("--clear", "session_context.open_prs", "--by", "test")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(self._read()["session_context"]["open_prs"], [])
        self.assertNotIn("usage:", result.stdout, "no-op 吐 help = 修复前行为回归")

    def test_add_dotted_open_prs_no_orphan(self):
        """--add session_context.open_prs 落 nested，不造字面点号孤儿键（V37.9.270 家族）"""
        result = self._run("--add", "session_context.open_prs", "pr-new", "--by", "test")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        data = self._read()
        self.assertIn("pr-new", data["session_context"]["open_prs"])
        self.assertNotIn("session_context.open_prs", data,
                         "字面点号顶层孤儿键 = V37.9.270 血案机制复活")

    def test_pop_dotted_open_prs(self):
        """--pop session_context.open_prs 0 弹出 nested 首项"""
        result = self._run("--pop", "session_context.open_prs", "0", "--by", "test")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("stale-pr-entry", result.stdout)
        self.assertEqual(self._read()["session_context"]["open_prs"], [])


if __name__ == "__main__":
    unittest.main()
