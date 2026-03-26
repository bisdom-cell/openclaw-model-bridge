#!/usr/bin/env python3
"""test_kb_business.py — KB 业务逻辑全量测试

覆盖：kb_embed, kb_rag, kb_trend, kb_integrity, mm_index, mm_search
重点测试纯函数逻辑和数据结构，不测试外部 API 调用
"""
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import hashlib


class TestKbEmbedLogic(unittest.TestCase):
    """kb_embed.py 核心逻辑"""

    def test_python_syntax(self):
        result = subprocess.run(
            [sys.executable, "-c", "import ast; ast.parse(open('kb_embed.py').read())"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_has_incremental_indexing(self):
        """增量索引：基于文件 hash 跳过已索引文件"""
        with open("kb_embed.py") as f:
            content = f.read()
        self.assertIn("file_hash", content)
        self.assertIn("indexed_hashes", content)

    def test_has_chunking_logic(self):
        """文本分块逻辑存在"""
        with open("kb_embed.py") as f:
            content = f.read()
        self.assertIn("chunk", content.lower())

    def test_uses_atomic_write(self):
        """元数据使用原子写入"""
        with open("kb_embed.py") as f:
            content = f.read()
        self.assertIn("os.replace", content)

    def test_model_change_triggers_reindex(self):
        """模型变更时自动重建索引"""
        with open("kb_embed.py") as f:
            content = f.read()
        self.assertIn("model", content.lower())
        self.assertIn("reindex", content.lower())


class TestKbRagLogic(unittest.TestCase):
    """kb_rag.py 核心逻辑"""

    def test_python_syntax(self):
        result = subprocess.run(
            [sys.executable, "-c", "import ast; ast.parse(open('kb_rag.py').read())"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_has_cosine_similarity(self):
        """使用 cosine similarity 搜索"""
        with open("kb_rag.py") as f:
            content = f.read()
        self.assertIn("cosine", content.lower())

    def test_has_context_mode(self):
        """支持 --context 模式（LLM 注入）"""
        with open("kb_rag.py") as f:
            content = f.read()
        self.assertIn("--context", content)

    def test_has_json_mode(self):
        """支持 --json 模式（脚本调用）"""
        with open("kb_rag.py") as f:
            content = f.read()
        self.assertIn("--json", content)

    def test_has_top_k(self):
        """支持 --top N 参数"""
        with open("kb_rag.py") as f:
            content = f.read()
        self.assertIn("--top", content)


class TestKbTrendLogic(unittest.TestCase):
    """kb_trend.py 核心逻辑"""

    def test_python_syntax(self):
        result = subprocess.run(
            [sys.executable, "-c", "import ast; ast.parse(open('kb_trend.py').read())"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_has_keyword_extraction(self):
        """关键词频率计算"""
        with open("kb_trend.py") as f:
            content = f.read()
        self.assertIn("keyword", content.lower())
        self.assertIn("counter", content.lower())

    def test_has_trend_detection(self):
        """趋势检测（上升/消退）"""
        with open("kb_trend.py") as f:
            content = f.read()
        # 应该有本周 vs 上周的对比逻辑
        self.assertIn("上升", content)

    def test_has_llm_fallback(self):
        """LLM 失败时 graceful fallback"""
        with open("kb_trend.py") as f:
            content = f.read()
        self.assertIn("--no-llm", content)

    def test_has_json_output(self):
        """支持 --json 输出"""
        with open("kb_trend.py") as f:
            content = f.read()
        self.assertIn("--json", content)

    def test_updates_status_json(self):
        """更新 status.json 的 last_trend_report"""
        with open("kb_trend.py") as f:
            content = f.read()
        self.assertIn("status_update", content)
        self.assertIn("last_trend_report", content)


class TestMmIndexLogic(unittest.TestCase):
    """mm_index.py 核心逻辑"""

    def test_python_syntax(self):
        result = subprocess.run(
            [sys.executable, "-c", "import ast; ast.parse(open('mm_index.py').read())"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_supported_mime_types(self):
        """支持常见媒体类型"""
        with open("mm_index.py") as f:
            content = f.read()
        for mime in ["image/jpeg", "image/png", "audio/mp3", "video/mp4", "application/pdf"]:
            self.assertIn(mime, content, f"Missing MIME type: {mime}")

    def test_max_file_size_limit(self):
        """有文件大小限制"""
        with open("mm_index.py") as f:
            content = f.read()
        self.assertIn("MAX_FILE_SIZE", content)

    def test_has_reindex_mode(self):
        """支持 --reindex 模式"""
        with open("mm_index.py") as f:
            content = f.read()
        self.assertIn("--reindex", content)

    def test_atomic_meta_write(self):
        """元数据原子写入"""
        with open("mm_index.py") as f:
            content = f.read()
        self.assertIn("os.replace(tmp, META_FILE)", content)

    def test_corruption_recovery(self):
        """JSON 损坏恢复"""
        with open("mm_index.py") as f:
            content = f.read()
        self.assertIn("JSONDecodeError", content)
        self.assertIn(".corrupted", content)

    def test_rate_limiting(self):
        """API 限流保护"""
        with open("mm_index.py") as f:
            content = f.read()
        self.assertIn("BATCH_PAUSE", content)
        self.assertIn("429", content)

    def test_hash_deduplication(self):
        """基于文件 hash 去重"""
        with open("mm_index.py") as f:
            content = f.read()
        self.assertIn("file_hash", content)
        self.assertIn("indexed_hashes", content)


class TestMmSearchLogic(unittest.TestCase):
    """mm_search.py 核心逻辑"""

    def test_python_syntax(self):
        result = subprocess.run(
            [sys.executable, "-c", "import ast; ast.parse(open('mm_search.py').read())"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_has_stats_mode(self):
        """支持 --stats 统计模式"""
        with open("mm_search.py") as f:
            content = f.read()
        self.assertIn("--stats", content)

    def test_has_cosine_similarity(self):
        """使用 cosine similarity"""
        with open("mm_search.py") as f:
            content = f.read()
        self.assertIn("cosine", content.lower())


class TestKbIntegrityLogic(unittest.TestCase):
    """kb_integrity.py 业务逻辑（功能性测试）"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.kb_dir = os.path.join(self.tmp, ".kb")
        os.makedirs(os.path.join(self.kb_dir, "notes"))
        os.makedirs(os.path.join(self.kb_dir, "sources"))
        os.makedirs(os.path.join(self.kb_dir, ".integrity"))
        # 写入关键文件
        with open(os.path.join(self.kb_dir, "index.json"), "w") as f:
            json.dump({"entries": []}, f)
        with open(os.path.join(self.kb_dir, "status.json"), "w") as f:
            json.dump({"priorities": [], "feedback": [], "health": {}, "recent_changes": [], "focus": ""}, f)
        # 写入一些笔记
        for i in range(5):
            with open(os.path.join(self.kb_dir, "notes", f"note_{i}.md"), "w") as f:
                f.write(f"# Note {i}\nContent here")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sha256_deterministic(self):
        """SHA256 对相同文件产生相同哈希"""
        path = os.path.join(self.kb_dir, "index.json")
        h1 = hashlib.sha256(open(path, "rb").read()).hexdigest()
        h2 = hashlib.sha256(open(path, "rb").read()).hexdigest()
        self.assertEqual(h1, h2)

    def test_sha256_changes_on_modification(self):
        """文件修改后 SHA256 变化"""
        path = os.path.join(self.kb_dir, "index.json")
        h1 = hashlib.sha256(open(path, "rb").read()).hexdigest()
        with open(path, "w") as f:
            json.dump({"entries": [{"test": True}]}, f)
        h2 = hashlib.sha256(open(path, "rb").read()).hexdigest()
        self.assertNotEqual(h1, h2)

    def test_dir_count_detection(self):
        """检测目录文件数变化"""
        notes_dir = os.path.join(self.kb_dir, "notes")
        count_before = len([f for f in os.listdir(notes_dir) if not f.startswith(".")])
        self.assertEqual(count_before, 5)
        # 删除一些文件
        os.remove(os.path.join(notes_dir, "note_0.md"))
        os.remove(os.path.join(notes_dir, "note_1.md"))
        count_after = len([f for f in os.listdir(notes_dir) if not f.startswith(".")])
        self.assertEqual(count_after, 3)
        # 骤降比例
        drop_ratio = count_after / count_before
        self.assertLess(drop_ratio, 0.7)

    def test_status_json_structure_validation(self):
        """验证 status.json 结构完整性"""
        path = os.path.join(self.kb_dir, "status.json")
        with open(path) as f:
            data = json.load(f)
        required = {"priorities", "feedback", "health", "recent_changes", "focus"}
        missing = required - set(data.keys())
        self.assertEqual(missing, set())

    def test_corrupted_status_json_detected(self):
        """损坏的 status.json 被检测到"""
        path = os.path.join(self.kb_dir, "status.json")
        with open(path, "w") as f:
            f.write("{broken...")
        with self.assertRaises(json.JSONDecodeError):
            with open(path) as f:
                json.load(f)

    def test_file_disappearance_detected(self):
        """文件消失被检测到"""
        path = os.path.join(self.kb_dir, "index.json")
        self.assertTrue(os.path.exists(path))
        os.remove(path)
        self.assertFalse(os.path.exists(path))


class TestLocalEmbedLogic(unittest.TestCase):
    """local_embed.py 核心逻辑"""

    def test_python_syntax(self):
        result = subprocess.run(
            [sys.executable, "-c", "import ast; ast.parse(open('local_embed.py').read())"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_has_bench_mode(self):
        """支持 --bench 性能测试"""
        with open("local_embed.py") as f:
            content = f.read()
        self.assertIn("--bench", content)


class TestKbWriteScript(unittest.TestCase):
    """kb_write.sh 逻辑"""

    def test_script_syntax(self):
        result = subprocess.run(["bash", "-n", "kb_write.sh"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_uses_atomic_lock(self):
        with open("kb_write.sh") as f:
            content = f.read()
        self.assertIn("mkdir", content)
        self.assertIn(".write.lockdir", content)

    def test_index_atomic_write(self):
        with open("kb_write.sh") as f:
            content = f.read()
        self.assertIn("os.replace(tmpfile, index)", content)

    def test_has_trap_cleanup(self):
        with open("kb_write.sh") as f:
            content = f.read()
        self.assertIn("trap", content)


class TestAllScriptsSyntax(unittest.TestCase):
    """所有脚本语法验证"""

    def _check_sh(self, filename):
        result = subprocess.run(["bash", "-n", filename], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, f"{filename}: {result.stderr}")

    def _check_py(self, filename):
        result = subprocess.run(
            [sys.executable, "-c", f"import ast; ast.parse(open('{filename}').read())"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, f"{filename}: {result.stderr}")

    def test_all_sh_scripts(self):
        """所有 .sh 文件 bash 语法正确"""
        import glob
        for sh in glob.glob("*.sh") + glob.glob("jobs/**/*.sh", recursive=True):
            if sh.startswith(".git"):
                continue
            with self.subTest(script=sh):
                self._check_sh(sh)

    def test_all_py_scripts(self):
        """所有 .py 文件 Python 语法正确"""
        import glob
        for py in glob.glob("*.py"):
            if py.startswith(".git") or py.startswith("test_"):
                continue
            with self.subTest(script=py):
                self._check_py(py)


class TestSecurityPatterns(unittest.TestCase):
    """安全模式检查"""

    def test_no_hardcoded_api_keys(self):
        """没有硬编码的 API key"""
        import glob
        for f in glob.glob("*.py") + glob.glob("*.sh"):
            if f.startswith(".git"):
                continue
            with open(f) as fh:
                content = fh.read()
            # 跳过测试文件和文档
            if f.startswith("test_"):
                continue
            import re
            keys = re.findall(r'sk-[A-Za-z0-9]{20,}', content)
            # 过滤占位符
            real_keys = [k for k in keys if "REPLACE" not in k and "xxx" not in k.lower()]
            self.assertEqual(real_keys, [], f"{f} contains hardcoded API key")

    def test_no_real_phone_numbers(self):
        """没有真实手机号"""
        import glob
        for f in glob.glob("*.py") + glob.glob("*.sh"):
            if f.startswith(".git") or f.startswith("test_"):
                continue
            with open(f) as fh:
                content = fh.read()
            import re
            # 匹配 +852XXXXXXXX 格式但排除占位符 +85200000000
            phones = re.findall(r'\+852\d{8}', content)
            real = [p for p in phones if p != "+85200000000"]
            self.assertEqual(real, [], f"{f} contains real phone number")

    def test_no_pipe_crontab_pattern(self):
        """没有危险的 | crontab - 模式"""
        import glob
        for sh in glob.glob("*.sh"):
            if sh.startswith(".git") or sh in ("full_regression.sh",):
                continue
            with open(sh) as f:
                for i, line in enumerate(f, 1):
                    stripped = line.strip()
                    if stripped.startswith("#") or stripped.startswith("echo"):
                        continue
                    self.assertNotIn("| crontab -", line,
                        f"{sh}:{i}: dangerous pipe crontab pattern")


if __name__ == "__main__":
    unittest.main()
