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

    def test_v37_9_319_coverage_never_rounds_up_to_100(self):
        """V37.9.319: 100% 只能在真的全覆盖时出现。

        2026-08-18 preflight --full 实录: 「KB 索引覆盖 100%（19 个待索引）」同句
        自相矛盾 —— `{pct:.0f}` 把 ≥99.5% 四舍五入成 100%, 而那 19 个文件(含一个
        11808 字符笔记)确实不在索引里、PA 搜不到, 要等次日 03:30 cron。操作者读到
        100% 的结论是"无事可做" = 四舍五入制造的假安心。preflight 正是拿这个数字
        判 KB 健康 (≥90 → warn 而非 fail), 不能让它虚高。
        """
        with open("kb_embed.py", encoding="utf-8") as f:
            src = f.read()
        # 未全覆盖时必须钳位, 不得让 .0f 进位到 100
        self.assertIn("if indexed_count < total_files:", src)
        self.assertIn("coverage_pct = min(coverage_pct, 99.9)", src)
        self.assertIn("if total_indexed_chars < total_source_chars:", src)
        self.assertIn("char_pct = min(char_pct, 99.9)", src)
        # 退役 .0f (一位小数才能让 99.9 与 100.0 可区分)
        self.assertNotIn("({coverage_pct:.0f}%)", src)
        self.assertNotIn("({char_pct:.0f}%)", src)
        self.assertIn("({coverage_pct:.1f}%)", src)

    def test_v37_9_319_clamp_arithmetic(self):
        """行为级: 复现钳位算术 (源码是 verify() 内联, 此处验证不变式本身)。"""
        def clamp(indexed, total):
            pct = indexed / max(total, 1) * 100
            if indexed < total:
                pct = min(pct, 99.9)
            return f"{pct:.1f}"
        # 两个机制各管一段, 分别断言:
        # (1) .1f 管 99.5~99.94 —— 血案场景 3800 文件 19 未索引 = 99.5%,
        #     旧 `.0f` 进位成 "100%" (2026-08-18 实录), 现忠实显示 99.5
        self.assertEqual(clamp(3781, 3800), "99.5")
        self.assertEqual(f"{3781/3800*100:.0f}", "100")   # 反向: 证旧格式确会进位
        # (2) 钳位管 >99.94 —— 仅 1 个未索引时 .1f 仍会进位到 100.0, 钳到 99.9
        self.assertEqual(clamp(3999, 4000), "99.9")
        self.assertEqual(f"{3999/4000*100:.1f}", "100.0")  # 反向: 证不钳位会进位
        # 真全覆盖才允许 100.0
        self.assertEqual(clamp(3800, 3800), "100.0")
        # 低覆盖不受影响
        self.assertEqual(clamp(2000, 3800), "52.6")

    def test_v37_9_319_preflight_extracts_decimal_pct(self):
        """preflight 的 COV_PCT 提取必须容忍一位小数 (否则 99.9% 抽出空 → 落 0 → 误 fail)。"""
        import re
        with open("preflight_check.sh", encoding="utf-8") as f:
            pf = f.read()
        # V37.9.322: 退役断言只看**可执行行** —— 守卫的意图是「preflight 不得再
        # **打印**这句硬编码断言」, 注释里解释退役历史是合法文档。此前不剥注释,
        # 于是 V37.9.322 在同文件写「…与 V37.9.319 硬编码「KB 索引 100% 覆盖」
        # 同一 bug 类」时当场把这条守卫咬红 (V37.9.178 家族)。
        pf_exec = "".join(l for l in pf.splitlines(keepends=True)
                          if not l.lstrip().startswith("#"))
        self.assertIn(r"[0-9]+(\.[0-9]+)?%", pf_exec)
        self.assertNotIn("KB 索引 100% 覆盖", pf_exec)   # 退役硬编码 100% 文案
        # 防空转: 剥注释后仍是 preflight 本体
        self.assertIn("COV_PCT", pf_exec)
        # 行为: 用脚本里的同款模式抽整数部分
        pat = re.compile(r"[0-9]+(?:\.[0-9]+)?%")
        for text, want in (("文件覆盖: 3781/3800 (99.9%)", "99"),
                           ("文件覆盖: 3800/3800 (100.0%)", "100"),
                           ("文件覆盖: 2000/3800 (52.6%)", "52")):
            m = pat.search(text)
            self.assertIsNotNone(m, text)
            self.assertEqual(re.match(r"^[0-9]+", m.group(0)).group(0), want)

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

    def test_chunk_arxiv_source(self):
        """ArXiv source 格式：按 *标题* 行切分条目"""
        from kb_embed import chunk_text
        text = """## 2025-03-15
*Qwen3: A New Foundation Model*
作者：Author 等 | 日期：2025-03-15
链接：https://arxiv.org/abs/2503.12345
贡献：核心贡献说明
价值：⭐⭐⭐

*LLaMA 4: Scaling Language Models*
作者：Meta 等 | 日期：2025-03-15
链接：https://arxiv.org/abs/2503.67890
贡献：大规模扩展
价值：⭐⭐⭐⭐"""
        chunks = chunk_text(text, "sources/arxiv_daily.md", source_type="source")
        self.assertGreater(len(chunks), 0)
        # 每条论文应该是一个独立条目（可能与日期头合并）
        all_text = " ".join(c[0] for c in chunks)
        self.assertIn("Qwen3", all_text)
        self.assertIn("LLaMA 4", all_text)

    def test_chunk_hn_source(self):
        """HN source 格式：按 - **[Title] 行切分"""
        from kb_embed import chunk_text
        lines = []
        for i in range(20):
            lines.append(f"- **[Article {i}](https://hn.item/{i})** | 2025-03-15 | 要点：要点{i} | ⭐⭐⭐")
        text = "## 2025-03-15\n" + "\n".join(lines)
        chunks = chunk_text(text, "sources/hn_daily.md", source_type="source")
        self.assertGreater(len(chunks), 0)
        all_text = " ".join(c[0] for c in chunks)
        # 所有条目都应被索引
        for i in range(20):
            self.assertIn(f"Article {i}", all_text)

    def test_chunk_freight_source(self):
        """Freight source 格式：按数字序号切分"""
        from kb_embed import chunk_text
        text = """## 2025-03-15 08:00
1. 企业信号：[某某] — 需求描述一
行动：建议一
评级：⭐⭐⭐
链接：https://example.com/1
2. 企业信号：[某某二] — 需求描述二
行动：建议二
评级：⭐⭐⭐⭐
链接：https://example.com/2"""
        chunks = chunk_text(text, "sources/freight_daily.md", source_type="source")
        self.assertGreater(len(chunks), 0)
        all_text = " ".join(c[0] for c in chunks)
        self.assertIn("某某", all_text)
        self.assertIn("某某二", all_text)

    def test_chunk_note_unchanged(self):
        """note 类文件仍按 \\n\\n 分段（行为不变）"""
        from kb_embed import chunk_text
        text = "第一段内容，这是一些笔记。\n\n第二段内容，继续写。\n\n第三段。"
        chunks = chunk_text(text, "notes/test.md", source_type="note")
        self.assertGreater(len(chunks), 0)
        all_text = " ".join(c[0] for c in chunks)
        self.assertIn("第一段", all_text)
        self.assertIn("第三段", all_text)

    def test_chunk_source_no_content_loss(self):
        """source 切分零内容丢失"""
        from kb_embed import chunk_text
        entries = []
        for i in range(50):
            entries.append(f"*Paper {i}: A Study on Topic {i}*\n作者：Author {i}\n链接：https://example.com/{i}\n价值：⭐⭐⭐")
        text = "## 2025-03-15\n" + "\n".join(entries)
        chunks = chunk_text(text, "sources/test.md", source_type="source")
        all_text = " ".join(c[0] for c in chunks)
        for i in range(50):
            self.assertIn(f"Paper {i}", all_text, f"Paper {i} lost in chunking")

    def test_split_source_entries(self):
        """_split_source_entries 正确识别条目边界"""
        from kb_embed import _split_source_entries
        text = """## 2025-03-15
*First Paper*
作者：Author
## 2025-03-16
*Second Paper*
作者：Author2"""
        entries = _split_source_entries(text)
        self.assertEqual(len(entries), 4)  # 2 date headers + 2 papers

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

    def test_v37_9_178_push_routes_through_notify(self):
        """V37.9.178: 周趋势报告经 notify.sh --topic daily（到微信），退役裸 os.system。"""
        with open("kb_trend.py") as f:
            content = f.read()
        self.assertIn("--topic daily", content)
        self.assertIn("V37.9.178", content)
        self.assertIn("subprocess.run", content)
        # 剥注释行后检查实际代码：不得再用 os.system 发 message（漏微信 + shell 注入风险）。
        # 注释里仍可提及该退役模式作文档（不参与匹配）。
        code = "\n".join(ln for ln in content.splitlines()
                         if not ln.lstrip().startswith("#"))
        self.assertNotRegex(code, r"os\.system\([^)]*message send",
                            "kb_trend 实际代码不得再用 os.system 发 message（漏微信 + 注入风险）")

    def test_v37_9_178_push_behavior_notify_route(self):
        """行为级: notify.sh 可达 → push_whatsapp 经 notify --topic daily（mock subprocess）。"""
        import kb_trend
        from unittest import mock
        with mock.patch("kb_trend.log"), \
             mock.patch("os.path.exists", return_value=True), \
             mock.patch("kb_trend.subprocess.run") as run:
            kb_trend.push_whatsapp("报告正文", [], [], [])
        self.assertTrue(run.called, "push_whatsapp 应调 subprocess.run")
        argv = run.call_args[0][0]
        self.assertEqual(argv[0], "bash")
        self.assertIn("--topic daily", argv[2])
        self.assertIn("notify", argv[2])
        # msg 作为独立 argv 元素传递（不拼进 shell 字符串 → 防注入）
        self.assertIn("周趋势报告", argv[-1])

    def test_v37_9_178_push_fallback_argv_safe(self):
        """notify.sh 不可达 → fallback subprocess argv（不经 shell，msg 多行/引号安全）。"""
        import kb_trend
        from unittest import mock
        with mock.patch("kb_trend.log"), \
             mock.patch("os.path.exists", return_value=False), \
             mock.patch("kb_trend.subprocess.run") as run:
            kb_trend.push_whatsapp('报告"含引号"\n多行', [], [], [])
        argv = run.call_args[0][0]
        self.assertIn("message", argv)
        self.assertIn("send", argv)
        mi = argv.index("--message")
        self.assertIn("周趋势报告", argv[mi + 1],
                      "msg 应作独立 argv 元素传递（不拼进 shell 字符串）")


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
            # V37.9.85: +85200000001 是 governance_ontology.yaml 内嵌单测 fixture
            real = [p for p in phones if p not in ("+85200000000", "+85200000001")]
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


class TestComputeWeekWindowsV280(unittest.TestCase):
    """V37.9.280 (对抗审计 KB-F4): kb_trend 周窗口 — 各恰 7 天、无共享边界日。

    原窗口 this_start=now-7d 且 last_end=this_start: extract_period_text 按
    YYYYMMDD 双端 inclusive → D-7 当天同时落两窗 (双计数 + 各 8 天), 恰在 D-7
    出现的关键词 ratio=1.0 永不上榜, 真消退关键词被阻尼。
    """

    def _date_set(self, start, end):
        from datetime import timedelta
        out, d = set(), start
        while d <= end:
            out.add(d.strftime("%Y%m%d"))
            d += timedelta(days=1)
        return out

    def test_windows_disjoint_and_seven_days_each(self):
        import kb_trend
        from datetime import datetime
        now = datetime(2026, 7, 26, 22, 30)
        ts, te, ls, le = kb_trend.compute_week_windows(now)
        this_days = self._date_set(ts, te)
        last_days = self._date_set(ls, le)
        self.assertEqual(len(this_days), 7)
        self.assertEqual(len(last_days), 7)
        self.assertEqual(this_days & last_days, set(),
                         "D-7 边界日双计数 → 趋势 ratio 被阻尼 (KB-F4 血案)")
        self.assertEqual(len(this_days | last_days), 14)

    def test_main_uses_helper(self):
        # 一物一形: main() 必须走 compute_week_windows, 不得内联第二份窗口算术
        with open("kb_trend.py") as f:
            src = f.read()
        self.assertIn("compute_week_windows(now)", src)
        self.assertNotIn("last_end = this_start", src,
                         "旧重叠窗口算术不得回归")


if __name__ == "__main__":
    unittest.main()
