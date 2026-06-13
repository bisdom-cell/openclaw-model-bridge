#!/usr/bin/env python3
"""
gen_compat_matrix.py — compatibility_matrix.md 漂移防护（V37.9.143，外部评审2 P0(b)）

镜像 gen_jobs_doc.py --check 模式：比对 providers.py 直出的三张表
（主矩阵 "## 支持的 Provider" + 验证档位 "## 验证档位" + 能力矩阵 "## 能力矩阵"）vs
docs/compatibility_matrix.md 中对应表格段。

机器比对范围契约（unfinished #25(b) 原文 + V37.9.146 扩展）：
  - 只比 providers.py 直出的三张表
    (matrix_table_lines / tier_table_lines / capability_table_lines)
  - 人工段落（Fallback 降级路径 / 添加新 Provider / 工具模式验证）
    不参与机器比对，--fix 也绝不触碰

血案背景：外部评审2 (2026-06-11) 抓到 compatibility_matrix.md 停在 2026-04-05
七 provider（V37.9.52 Doubao 加入后 doc 未刷新 2 个月）。V37.9.142 手动刷新，
本工具把"手动刷新"升级为"机器守卫"——首次比对即抓到 V37.9.142 手动刷新自身
遗漏的真漂移（Doubao json_mode=True 被手写为 —）。
V37.9.146（外部评审2 P2(a)）: verification_tier 字段化, "验证档位"表从手写人工段落
升级为第 3 张机器守卫表 (退役手写表 = 一物一形)。

用法：
  python3 gen_compat_matrix.py            # 打印三张表（stdout）
  python3 gen_compat_matrix.py --check    # 漂移检测（exit 0 = OK, 1 = drift）
  python3 gen_compat_matrix.py --fix      # 重写 doc 中三个表格段（不碰人工段落）
"""
import os
import sys

DOC_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "docs", "compatibility_matrix.md")

# (doc 二级标题, providers.py registry 方法名) — 机器比对的三张表
# 顺序跟 doc 阅读顺序 (支持的 Provider → 验证档位 → 能力矩阵), extract 本身顺序无关
TABLE_SPECS = [
    ("支持的 Provider", "matrix_table_lines"),
    ("验证档位", "tier_table_lines"),  # V37.9.146 字段化
    ("能力矩阵", "capability_table_lines"),
]


def log(msg):
    """诊断输出写 stderr（MR-11: 防 $(...) 命令替换污染 stdout）。"""
    print(msg, file=sys.stderr)


def generate_tables():
    """从 providers.py registry 直出两张表。

    返回 dict: {heading: [table lines]}（单一真理源 = providers.py）。
    """
    from providers import _default_registry
    return {
        heading: getattr(_default_registry, method)()
        for heading, method in TABLE_SPECS
    }


def extract_table_block(doc_lines, heading):
    """定位 `## <heading>` 标题后第一个 Markdown 表格块。

    返回 (start_idx, end_idx)，doc_lines[start_idx:end_idx] 是连续的 `|` 开头行；
    标题或表格缺失返回 (None, None)。
    """
    heading_line = f"## {heading}"
    h_idx = None
    for i, line in enumerate(doc_lines):
        if line.strip() == heading_line:
            h_idx = i
            break
    if h_idx is None:
        return None, None

    start = None
    for i in range(h_idx + 1, len(doc_lines)):
        stripped = doc_lines[i].strip()
        if stripped.startswith("|"):
            start = i
            break
        if stripped.startswith("## "):
            # 下一个标题之前没有表格
            return None, None
    if start is None:
        return None, None

    end = start
    while end < len(doc_lines) and doc_lines[end].strip().startswith("|"):
        end += 1
    return start, end


def check_drift(doc_path=DOC_PATH):
    """比对 doc 两个表格段 vs providers.py 直出表。返回漂移描述列表（空 = 无漂移）。"""
    if not os.path.exists(doc_path):
        return [f"doc 不存在: {doc_path}"]

    with open(doc_path, encoding="utf-8") as f:
        doc_lines = f.read().splitlines()

    expected = generate_tables()
    drifts = []
    for heading, _ in TABLE_SPECS:
        start, end = extract_table_block(doc_lines, heading)
        if start is None:
            drifts.append(f"[{heading}] doc 中找不到标题或表格段")
            continue
        actual = [l.rstrip() for l in doc_lines[start:end]]
        exp = [l.rstrip() for l in expected[heading]]
        if actual != exp:
            # 逐行 diff 摘要（最多 6 行差异）
            detail = []
            for i in range(max(len(actual), len(exp))):
                a = actual[i] if i < len(actual) else "<缺行>"
                e = exp[i] if i < len(exp) else "<多行>"
                if a != e:
                    detail.append(f"    doc:      {a}")
                    detail.append(f"    expected: {e}")
                if len(detail) >= 6:
                    detail.append("    ...")
                    break
            drifts.append(f"[{heading}] 表格段与 providers.py 直出不一致:\n" +
                          "\n".join(detail))
    return drifts


def fix_drift(doc_path=DOC_PATH):
    """重写 doc 中两个表格段为 providers.py 直出内容。人工段落不触碰。

    返回 True = 有修改写入, False = 已一致无修改。
    """
    with open(doc_path, encoding="utf-8") as f:
        doc_lines = f.read().splitlines()

    expected = generate_tables()
    changed = False
    for heading, _ in TABLE_SPECS:
        start, end = extract_table_block(doc_lines, heading)
        if start is None:
            log(f"WARN: [{heading}] doc 中找不到表格段, 跳过（请手动补标题后重跑）")
            continue
        exp = expected[heading]
        if [l.rstrip() for l in doc_lines[start:end]] != [l.rstrip() for l in exp]:
            doc_lines[start:end] = exp
            changed = True
            log(f"FIXED: [{heading}] 表格段已重写为 providers.py 直出内容")

    if changed:
        with open(doc_path, "w", encoding="utf-8") as f:
            f.write("\n".join(doc_lines) + "\n")
    return changed


def main():
    if "--check" in sys.argv:
        drifts = check_drift()
        if drifts:
            log("DRIFT: docs/compatibility_matrix.md 与 providers.py 直出表不一致:")
            for d in drifts:
                log(f"  - {d}")
            log("修复: python3 gen_compat_matrix.py --fix")
            return 1
        print("OK: compatibility_matrix 三张表与 providers.py 一致")
        return 0

    if "--fix" in sys.argv:
        changed = fix_drift()
        print("已修复表格段漂移" if changed else "无漂移, doc 未修改")
        return 0

    # 默认: 打印两张表
    tables = generate_tables()
    for heading, _ in TABLE_SPECS:
        print(f"## {heading}\n")
        print("\n".join(tables[heading]))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
