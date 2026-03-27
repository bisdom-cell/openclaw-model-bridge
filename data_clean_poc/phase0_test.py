#!/usr/bin/env python3
"""
Phase 0: 验证 Qwen3 对脏数据的判断力
核心假设: LLM 能准确识别数据质量问题并生成合理的清洗策略

测试方法:
  1. 读取 CSV 样本 → 生成数据概况（纯 Python，不依赖 pandas）
  2. 将概况发送给 LLM → 要求输出结构化的质量报告 + 清洗计划
  3. 评估 LLM 输出的准确性和可执行性

用法:
  python3 phase0_test.py                    # 测试全部 3 个样本
  python3 phase0_test.py --sample 1         # 只测试样本 1
  python3 phase0_test.py --dry-run          # 只生成 prompt，不调 LLM
  python3 phase0_test.py --endpoint URL     # 指定 LLM 端点（默认 localhost:5002）
"""

import csv
import json
import os
import sys
import argparse
import time
from collections import Counter
from urllib.request import Request, urlopen
from urllib.error import URLError

# ── 样本文件及已知问题（ground truth）──────────────────────

SAMPLES = {
    "sample1_orders.csv": {
        "description": "订单数据 — 日期混乱 + 重复 + 拼写错误 + 异常值",
        "known_issues": [
            "duplicate_rows: 行1001和1004完全重复",
            "mixed_date_formats: YYYY-MM-DD, DD/MM/YYYY, M/D/YYYY, YYYY/MM/DD, N/A, TBD",
            "typos_in_status: ative→active, inactvie→inactive",
            "encoding_conflict: status列混用文本(active/inactive)和数字(0)",
            "missing_values: amount和email有缺失",
            "negative_amount: 行1010金额为-50（异常或退款）",
            "invalid_email: sunba@缺少域名",
            "whitespace: 王五名字有前后空格",
            "case_inconsistency: Active/ACTIVE/active, LISI@QQ.COM",
            "zero_amount: 行1003金额为0（可能异常）",
        ],
    },
    "sample2_products.csv": {
        "description": "商品数据 — 语义重复 + 分类不一致 + 测试数据混入",
        "known_issues": [
            "semantic_duplicate: SKU-001和SKU-002是同一商品（大小写不同）",
            "semantic_duplicate: SKU-004和SKU-005是同一商品（分类中英文不同）",
            "semantic_duplicate: SKU-006和SKU-007（括号全半角+分类不同）",
            "semantic_duplicate: SKU-008和SKU-009（供应商中英文不同）",
            "semantic_duplicate: SKU-014和SKU-015（供应商中英文不同）",
            "negative_stock: SKU-003库存为-5",
            "non_numeric_stock: SKU-013库存为'thirty'（文本）",
            "test_data: SKU-010（空名称）和SKU-011（测试商品）应删除",
            "category_inconsistency: Electronics/电子产品, Clothing/服装, Food/食品 混用中英文",
            "zero_price: SKU-010价格为0",
        ],
    },
    "sample3_contacts.csv": {
        "description": "联系人数据 — 跨行重复 + 格式混乱 + 无效值 + 测试数据",
        "known_issues": [
            "cross_row_duplicate: 行1和2是同一人（王建国，电话相同，公司写法不同）",
            "cross_row_duplicate: 行4和5是同一人（张伟/Zhang Wei，电话相同）",
            "cross_row_duplicate: 行7和12是赵丽颖重复记录",
            "phone_format_inconsistency: +86-xxx, +86 xxx, 纯数字, phone_unknown",
            "invalid_phone: 行6手机号少一位, 行13全零号码",
            "placeholder_values: NULL(行8名字), undefined(行15名字), N/A(行11公司)",
            "date_format_mixed: YYYY-MM-DD, YYYY/MM/DD, 'Jan 15 2025', 'not_a_date'",
            "vip_case_inconsistency: gold/Gold/GOLD, silver/Silver, platinum/PLATINUM",
            "city_inconsistency: 深圳/Shenzhen, 北京/Beijing",
            "test_data: 行13孙悟空+花果山疑似测试数据",
        ],
    },
}

# ── 轻量数据概况生成器（模拟 Profiler 工具输出）──────────

def profile_csv(filepath):
    """纯 Python 生成数据概况，不依赖 pandas"""
    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        return {"error": "empty file"}

    columns = list(rows[0].keys())
    profile = {
        "file": os.path.basename(filepath),
        "row_count": len(rows),
        "column_count": len(columns),
        "columns": {},
        "duplicate_rows": 0,
        "sample_rows": rows[:5],
    }

    # 检测完全重复行
    row_strs = [json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows]
    row_counts = Counter(row_strs)
    profile["duplicate_rows"] = sum(c - 1 for c in row_counts.values() if c > 1)

    for col in columns:
        values = [r[col] for r in rows]
        non_empty = [v for v in values if v and v.strip()]
        unique_vals = set(non_empty)

        # 类型推断
        numeric_count = 0
        for v in non_empty:
            try:
                float(v.replace(",", ""))
                numeric_count += 1
            except (ValueError, AttributeError):
                pass

        col_profile = {
            "total": len(values),
            "non_empty": len(non_empty),
            "missing_rate": f"{(len(values) - len(non_empty)) / len(values) * 100:.1f}%",
            "unique_count": len(unique_vals),
            "likely_type": "numeric" if numeric_count > len(non_empty) * 0.7 else "text",
            "sample_values": list(unique_vals)[:15],
        }

        # 值频率分布（top 10）
        val_counts = Counter(non_empty)
        col_profile["top_values"] = val_counts.most_common(10)

        profile["columns"][col] = col_profile

    return profile


# ── Prompt 构建 ──────────────────────────────────────────

SYSTEM_PROMPT = """你是一个专业的数据质量分析师。你的任务是：
1. 分析数据概况，识别所有数据质量问题
2. 对每个问题评估严重程度（high/medium/low）
3. 为每个问题提出具体的清洗策略
4. 标记哪些决策需要人工确认

请用以下 JSON 格式输出（不要输出其他内容）：
{
  "quality_score": 0-100的整数,
  "issues": [
    {
      "type": "问题类型（如 duplicate_rows, mixed_formats, missing_values 等）",
      "severity": "high/medium/low",
      "column": "受影响的列名（如适用）",
      "description": "问题描述（中文）",
      "affected_rows": "受影响的行数或行号",
      "cleaning_strategy": "具体清洗策略",
      "needs_human_review": true/false,
      "reason_for_review": "为什么需要人工确认（如适用）"
    }
  ],
  "cleaning_plan": [
    {
      "order": 1,
      "action": "操作名称",
      "description": "操作描述",
      "risk": "high/medium/low"
    }
  ]
}"""

def build_prompt(profile):
    """构建发给 LLM 的 prompt"""
    profile_str = json.dumps(profile, ensure_ascii=False, indent=2)
    return f"""请分析以下数据概况，识别所有数据质量问题并给出清洗方案。

## 数据概况

```json
{profile_str}
```

请按照系统提示中的 JSON 格式输出分析结果。"""


# ── LLM 调用 ────────────────────────────────────────────

def call_llm(endpoint, system_prompt, user_prompt, timeout=60):
    """调用 LLM（兼容 OpenAI API 格式）"""
    payload = {
        "model": "any",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 4096,
    }

    req = Request(
        f"{endpoint}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            content = result["choices"][0]["message"]["content"]
            # 尝试提取 JSON（LLM 可能包裹在 markdown code block 中）
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            return json.loads(content.strip())
    except json.JSONDecodeError as e:
        return {"error": f"LLM 输出非法 JSON: {e}", "raw": content}
    except URLError as e:
        return {"error": f"LLM 连接失败: {e}"}
    except Exception as e:
        return {"error": f"调用异常: {e}"}


# ── 评估逻辑 ────────────────────────────────────────────

def evaluate_result(llm_result, known_issues):
    """评估 LLM 识别的问题与已知问题的覆盖率"""
    if "error" in llm_result:
        return {"score": 0, "error": llm_result["error"]}

    issues_found = llm_result.get("issues", [])
    issues_text = json.dumps(issues_found, ensure_ascii=False).lower()

    # 检查每个已知问题是否被识别
    hits = []
    misses = []
    for known in known_issues:
        # 从 known issue 提取关键词
        issue_type = known.split(":")[0].strip().lower()
        issue_detail = known.split(":", 1)[1].strip().lower() if ":" in known else ""

        # 宽松匹配：关键词出现在 LLM 输出中即认为命中
        keywords = issue_type.replace("_", " ").split()
        # 也检查详情中的关键实体
        detail_keywords = []
        for word in issue_detail.replace("→", " ").replace("（", " ").replace("）", " ").split():
            if len(word) >= 2 and not word.isdigit():
                detail_keywords.append(word)

        matched = False
        # 类型关键词匹配
        if any(kw in issues_text for kw in keywords):
            matched = True
        # 详情关键词匹配（至少 2 个命中）
        detail_hits = sum(1 for kw in detail_keywords[:5] if kw in issues_text)
        if detail_hits >= 2:
            matched = True

        if matched:
            hits.append(known)
        else:
            misses.append(known)

    coverage = len(hits) / len(known_issues) * 100 if known_issues else 0

    return {
        "coverage": f"{coverage:.0f}%",
        "hits": len(hits),
        "total": len(known_issues),
        "issues_found_by_llm": len(issues_found),
        "missed": misses,
        "has_cleaning_plan": "cleaning_plan" in llm_result and len(llm_result.get("cleaning_plan", [])) > 0,
        "has_human_review_flags": any(i.get("needs_human_review") for i in issues_found),
        "quality_score": llm_result.get("quality_score", "N/A"),
    }


# ── 主流程 ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Phase 0: 数据清洗 LLM 判断力验证")
    parser.add_argument("--sample", type=int, choices=[1, 2, 3], help="只测试指定样本")
    parser.add_argument("--dry-run", action="store_true", help="只生成 prompt，不调 LLM")
    parser.add_argument("--endpoint", default="http://localhost:5002", help="LLM 端点")
    parser.add_argument("--timeout", type=int, default=90, help="LLM 调用超时（秒）")
    args = parser.parse_args()

    sample_dir = os.path.dirname(os.path.abspath(__file__))
    sample_files = list(SAMPLES.keys())
    if args.sample:
        sample_files = [sample_files[args.sample - 1]]

    results = {}
    pass_count = 0
    total = len(sample_files)

    for filename in sample_files:
        info = SAMPLES[filename]
        filepath = os.path.join(sample_dir, filename)

        print(f"\n{'='*60}")
        print(f"样本: {filename}")
        print(f"描述: {info['description']}")
        print(f"已知问题数: {len(info['known_issues'])}")
        print(f"{'='*60}")

        # Step 1: 生成数据概况
        profile = profile_csv(filepath)
        print(f"行数: {profile['row_count']}, 列数: {profile['column_count']}, 重复行: {profile['duplicate_rows']}")

        # Step 2: 构建 prompt
        prompt = build_prompt(profile)

        if args.dry_run:
            print(f"\n--- PROMPT ({len(prompt)} chars) ---")
            print(prompt[:500] + "..." if len(prompt) > 500 else prompt)
            print("--- END PROMPT ---")
            # dry-run 模式下保存完整 prompt 到文件
            prompt_file = os.path.join(sample_dir, f"prompt_{filename.replace('.csv', '')}.txt")
            with open(prompt_file, "w") as f:
                f.write(f"=== SYSTEM ===\n{SYSTEM_PROMPT}\n\n=== USER ===\n{prompt}")
            print(f"完整 prompt 已保存到: {prompt_file}")
            continue

        # Step 3: 调用 LLM
        print(f"\n调用 LLM ({args.endpoint})...")
        start = time.time()
        llm_result = call_llm(args.endpoint, SYSTEM_PROMPT, prompt, timeout=args.timeout)
        elapsed = time.time() - start
        print(f"耗时: {elapsed:.1f}s")

        if "error" in llm_result:
            print(f"❌ 错误: {llm_result['error']}")
            if "raw" in llm_result:
                print(f"原始输出: {llm_result['raw'][:300]}")
            results[filename] = {"status": "error", "error": llm_result["error"]}
            continue

        # 保存 LLM 原始输出
        output_file = os.path.join(sample_dir, f"result_{filename.replace('.csv', '')}.json")
        with open(output_file, "w") as f:
            json.dump(llm_result, f, ensure_ascii=False, indent=2)
        print(f"LLM 输出已保存到: {output_file}")

        # Step 4: 评估
        evaluation = evaluate_result(llm_result, info["known_issues"])
        results[filename] = evaluation

        print(f"\n--- 评估结果 ---")
        print(f"质量评分: {evaluation['quality_score']}")
        print(f"问题覆盖率: {evaluation['coverage']} ({evaluation['hits']}/{evaluation['total']})")
        print(f"LLM 发现问题数: {evaluation['issues_found_by_llm']}")
        print(f"包含清洗计划: {'✅' if evaluation['has_cleaning_plan'] else '❌'}")
        print(f"标记人工审核: {'✅' if evaluation['has_human_review_flags'] else '❌'}")

        if evaluation["missed"]:
            print(f"\n未识别的问题:")
            for m in evaluation["missed"]:
                print(f"  ⚠️  {m}")

        # 通过标准: 覆盖率 >= 60% + 有清洗计划 + 有人工审核标记
        coverage_num = int(evaluation["coverage"].rstrip("%"))
        passed = coverage_num >= 60 and evaluation["has_cleaning_plan"] and evaluation["has_human_review_flags"]
        if passed:
            print(f"\n✅ 通过")
            pass_count += 1
        else:
            print(f"\n❌ 未通过")
            if coverage_num < 60:
                print(f"  原因: 覆盖率 {coverage_num}% < 60%")
            if not evaluation["has_cleaning_plan"]:
                print(f"  原因: 缺少清洗计划")
            if not evaluation["has_human_review_flags"]:
                print(f"  原因: 未标记需人工审核的项目")

    if not args.dry_run and results:
        print(f"\n{'='*60}")
        print(f"总结: {pass_count}/{total} 通过")
        print(f"Phase 0 {'✅ 验证通过' if pass_count == total else '⚠️ 部分未通过'}")
        print(f"{'='*60}")

        # 保存汇总
        summary_file = os.path.join(sample_dir, "phase0_summary.json")
        with open(summary_file, "w") as f:
            json.dump({
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "endpoint": args.endpoint,
                "pass_count": pass_count,
                "total": total,
                "verdict": "PASS" if pass_count == total else "PARTIAL",
                "results": results,
            }, f, ensure_ascii=False, indent=2)
        print(f"汇总已保存到: {summary_file}")


if __name__ == "__main__":
    main()
