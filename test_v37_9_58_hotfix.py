"""V37.9.58-hotfix source-level guard — V37.9.57 自动注入 HG_LEVEL_4_TEXT 必须配套 import os.

血案 (2026-05-12): V37.9.57 inject_level_4_to_aligned_jobs.py 自动给 8 个 ALIGNED
jobs 的 LLM call heredoc 末尾加了 `prompt += os.environ.get('HG_LEVEL_4_TEXT', '')`,
但**没补 import os**. 7 个 jobs 的 prompt-generation heredoc (line ~282-352 范围)
顶部是 `import sys, json` 缺 os → 每次 LLM 调用都因 NameError 抛异常 → retry 包装
当成 "成功返回空 content" → parse_6field_output("") 全空 → 用户看到只有英文标题+链接
+ 末尾"高对齐 0/N 条"虚假统计.

只有 emit heredoc (line ~388+) 在 V37.9.50-hotfix 时修过补 os, 但 prompt-generation
heredoc 是 V37.9.57 引入的第二处 os 调用点, 未同步补齐.

V37.9.58-hotfix: 修 7 个 jobs (run_hn_fixed.sh 也含同款 bug 已修) + 加本守卫防回归.

守卫契约 (机器化检测 V37.9.57 注入工具盲点):
  对每个含 `prompt += os.environ.get('HG_LEVEL_4_TEXT'` 的 .sh 脚本, 该行所在 heredoc
  (向上找最近 `<< 'PYEOF'`) 顶部第一行必须含 `import os` (无论组合形式).
"""

import os
import re
import unittest

# 仓库根 (test 文件应放仓库根)
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

# V37.9.57 注入的 8 个 ALIGNED jobs (清单与 inject_level_4_to_aligned_jobs.py 同源)
ALIGNED_JOB_SCRIPTS = [
    "run_hn_fixed.sh",
    "jobs/hf_papers/run_hf_papers.sh",
    "jobs/arxiv_monitor/run_arxiv.sh",
    "jobs/semantic_scholar/run_semantic_scholar.sh",
    "jobs/dblp/run_dblp.sh",
    "jobs/github_trending/run_github_trending.sh",
    "jobs/rss_blogs/run_rss_blogs.sh",
    "jobs/ai_leaders_x/run_ai_leaders_x.sh",
]

# HG_LEVEL_4_TEXT 注入字面量 (V37.9.57 注入工具的固定字符串)
HG_INJECTION_LITERAL = "prompt += os.environ.get('HG_LEVEL_4_TEXT'"

# heredoc 起点字面量
HEREDOC_START = "<< 'PYEOF'"


def find_heredoc_with_hg_injection(filepath):
    """返回所有含 HG_LEVEL_4_TEXT 注入的 heredoc (heredoc_start_lineno, import_line_lineno, import_line_text).
    向上找每个 prompt += os.environ.get(...) 最近的 << 'PYEOF' 作为 heredoc 起点,
    顶部 (heredoc_start + 1) 即 import 行.
    """
    with open(filepath, "r") as f:
        lines = f.readlines()
    result = []
    for i, line in enumerate(lines):
        if HG_INJECTION_LITERAL in line:
            # 往上找最近 heredoc 起点
            heredoc_start = None
            for j in range(i - 1, -1, -1):
                if HEREDOC_START in lines[j]:
                    heredoc_start = j
                    break
            if heredoc_start is not None:
                import_idx = heredoc_start + 1
                if import_idx < len(lines):
                    result.append((heredoc_start + 1, import_idx + 1, lines[import_idx]))
    return result


class TestV37958HotfixOsImport(unittest.TestCase):
    """V37.9.58-hotfix 守卫: V37.9.57 注入的 HG_LEVEL_4_TEXT prompt += os.environ
    必须配套 import os in 同一 heredoc 顶部, 否则触发 NameError 5/5 → broken push.
    """

    def test_v37_9_57_injection_present_in_all_8_jobs(self):
        """V37.9.57 注入了 HG_LEVEL_4_TEXT 在 8 个 ALIGNED jobs (1 是 hn + 7 是 jobs/)."""
        injected_count = 0
        for script in ALIGNED_JOB_SCRIPTS:
            fp = os.path.join(REPO_ROOT, script)
            self.assertTrue(os.path.exists(fp), f"V37.9.57 ALIGNED job {script} 不存在")
            with open(fp, "r") as f:
                src = f.read()
            if HG_INJECTION_LITERAL in src:
                injected_count += 1
        self.assertEqual(
            injected_count, 8,
            f"V37.9.57 注入应覆盖 8 个 ALIGNED jobs, 实际 {injected_count} 个含 HG_INJECTION_LITERAL"
        )

    def test_every_hg_injection_heredoc_has_os_import_at_top(self):
        """V37.9.58-hotfix 核心守卫: 每个含 HG_LEVEL_4_TEXT 注入的 heredoc 顶部必须含 import os.
        反向: 任一 heredoc 顶部缺 os → 触发 NameError → 5/5 LLM 调用失败 → 用户看到
        broken push (英文标题+链接, 无 6 字段).
        """
        broken = []
        for script in ALIGNED_JOB_SCRIPTS:
            fp = os.path.join(REPO_ROOT, script)
            for heredoc_lineno, import_lineno, import_line in find_heredoc_with_hg_injection(fp):
                # 取代码部分 (去掉行尾注释)
                code_part = import_line.split("#", 1)[0]
                # 检查 \bos\b 在 code part 中
                if not re.search(r"\bos\b", code_part):
                    broken.append(f"{script}: heredoc@line {heredoc_lineno} import line {import_lineno} = {import_line.strip()[:80]!r}")
        self.assertEqual(
            broken, [],
            "V37.9.58-hotfix 守卫违反: 以下 heredoc 顶部 import 缺 os 但 heredoc 内调用了 os.environ:\n"
            + "\n".join(broken)
            + "\n\n血案: V37.9.57 自动注入工具盲点 — 注入 os.environ.get(HG_LEVEL_4_TEXT) "
            + "但未补 import os, 每次 LLM 调用 NameError → broken push."
        )

    def test_hn_specific_fix_at_line_283(self):
        """V37.9.58-hotfix 锁定 HN 修复点 — line 283 必须含 import os."""
        fp = os.path.join(REPO_ROOT, "run_hn_fixed.sh")
        with open(fp, "r") as f:
            lines = f.readlines()
        # heredoc 起点 line 282 (HN 仅一个 HG_LEVEL_4_TEXT 注入)
        # import 行是 line 283 (1-indexed) → idx 282 (0-indexed)
        import_line = lines[282]
        self.assertIn("import sys, json", import_line, "HN line 283 应是 import sys, json 行")
        code_part = import_line.split("#", 1)[0]
        self.assertRegex(
            code_part, r"\bos\b",
            f"V37.9.58-hotfix: HN line 283 必须含 os import (V37.9.57 注入 prompt += os.environ.get) — "
            f"got: {import_line.strip()[:80]!r}"
        )

    def test_v37_9_58_hotfix_marker_in_at_least_one_fix(self):
        """至少一个 V37.9.58-hotfix 修复点应有 marker 注释便于运维 grep / 历史追溯."""
        marker_count = 0
        for script in ALIGNED_JOB_SCRIPTS:
            fp = os.path.join(REPO_ROOT, script)
            with open(fp, "r") as f:
                src = f.read()
            if "V37.9.58-hotfix" in src:
                marker_count += 1
        self.assertGreater(
            marker_count, 0,
            "V37.9.58-hotfix marker 至少应在一个 ALIGNED job 中, 便于 grep 追溯历史"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
