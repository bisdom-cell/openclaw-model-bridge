#!/usr/bin/env python3
"""
ontology_engine.py — Agent 工具本体引擎

从 tool_ontology.yaml 加载声明式规则，提供运行时查询接口。
proxy_filters.py 从此模块获取工具定义和策略，而非硬编码。

设计原则：
- 向后兼容：proxy_filters.py 的所有现有接口保持不变
- 渐进替换：先加载，后对比验证，最后切换
- 零外部依赖：只用标准库 + PyYAML（config_loader 已依赖）
"""

import json
import os
import sys

# PyYAML: config_loader 已依赖，确认可用
try:
    import yaml
except ImportError:
    yaml = None

# ---------------------------------------------------------------------------
# 本体文件路径
# ---------------------------------------------------------------------------
_ONTOLOGY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tool_ontology.yaml")


def _load_yaml(path):
    """加载 YAML 文件，返回 dict。"""
    if yaml is None:
        raise ImportError("PyYAML is required: pip install pyyaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class ToolOntology:
    """工具本体引擎 — 从 YAML 加载规则，提供运行时查询。

    核心职责：
    1. 解析 tool_ontology.yaml
    2. 生成 proxy_filters.py 所需的数据结构（ALLOWED_TOOLS, CLEAN_SCHEMAS, TOOL_PARAMS, CUSTOM_TOOLS 等）
    3. 提供规则查询接口（策略、别名、约束）
    4. 支持运行时重载（不重启服务）

    用法：
        onto = ToolOntology()            # 加载默认文件
        onto = ToolOntology(path="...")   # 加载指定文件
        onto = ToolOntology(data={...})   # 从 dict 加载（测试用）
    """

    def __init__(self, path=None, data=None):
        if data is not None:
            self._raw = data
        else:
            self._raw = _load_yaml(path or _ONTOLOGY_FILE)

        self._tools_section = self._raw.get("tools", {})
        self._policies = self._raw.get("policies", {})
        self._metadata = self._raw.get("metadata", {})

        # 解析后的缓存
        self._allowed_tools = None
        self._allowed_prefixes = None
        self._clean_schemas = None
        self._tool_params = None
        self._custom_tools = None
        self._aliases = None

        # 首次加载时解析
        self._parse()

    def _parse(self):
        """解析 YAML 为运行时数据结构。"""
        self._parse_allowed_tools()
        self._parse_schemas()
        self._parse_aliases()
        self._parse_custom_tools()

    # ── 工具白名单 ──

    def _parse_allowed_tools(self):
        builtin = self._tools_section.get("builtin", {})
        self._allowed_tools = set(builtin.keys())

        prefix_section = self._tools_section.get("prefix_matched", {})
        self._allowed_prefixes = list(prefix_section.keys())

        # 自定义工具名也加入（proxy 拦截判断用）
        custom = self._tools_section.get("custom", {})
        self._custom_tool_names = set(custom.keys())

    @property
    def allowed_tools(self) -> set:
        """白名单工具集合（精确匹配）。"""
        return self._allowed_tools

    @property
    def allowed_prefixes(self) -> list:
        """前缀匹配列表。"""
        return self._allowed_prefixes

    @property
    def custom_tool_names(self) -> set:
        """自定义工具名集合。"""
        return self._custom_tool_names

    # ── Schema 生成 ──

    def _parse_schemas(self):
        """从工具参数声明生成 OpenAI function calling schema。"""
        self._clean_schemas = {}
        self._tool_params = {}

        # 内置工具（只有声明了 parameters 的工具才生成 schema）
        for name, tool_def in self._tools_section.get("builtin", {}).items():
            params = tool_def.get("parameters")
            if params is not None and len(params) > 0:
                schema = self._build_schema(params)
                self._clean_schemas[name] = schema
                self._tool_params[name] = set(params.keys())
            elif params is not None and len(params) == 0:
                # 显式空参数（如 agents_list）
                self._clean_schemas[name] = {"type": "object", "properties": {}, "additionalProperties": False}
                self._tool_params[name] = set()
            # else: 没有 parameters 字段（如 image），不生成 schema

        # 前缀匹配工具（浏览器系列）
        for prefix, prefix_def in self._tools_section.get("prefix_matched", {}).items():
            for tool_name, tool_def in prefix_def.get("tools", {}).items():
                params = tool_def.get("parameters", {})
                self._tool_params[tool_name] = set(params.keys()) if params else set()

        # 自定义工具
        for name, tool_def in self._tools_section.get("custom", {}).items():
            params = tool_def.get("parameters", {})
            self._tool_params[name] = set(params.keys()) if params else set()

    def _build_schema(self, params):
        """从工具参数定义构建 OpenAI function calling JSON Schema。"""
        if not params:
            return {"type": "object", "properties": {}, "additionalProperties": False}

        properties = {}
        required = []
        for param_name, param_def in params.items():
            prop = {}
            ptype = param_def.get("type", "string")
            if ptype == "object":
                prop["type"] = "object"
            elif ptype == "integer":
                prop["type"] = "integer"
            else:
                prop["type"] = "string"

            if "description" in param_def:
                prop["description"] = param_def["description"]
            if "enum" in param_def:
                prop["enum"] = param_def["enum"]

            properties[param_name] = prop
            if param_def.get("required", False):
                required.append(param_name)

        schema = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        # 只对参数全部声明的工具加 additionalProperties: false
        schema["additionalProperties"] = False
        return schema

    @property
    def clean_schemas(self) -> dict:
        """工具 schema 字典（name → JSON Schema）。"""
        return self._clean_schemas

    @property
    def tool_params(self) -> dict:
        """工具合法参数集（name → set of param names）。"""
        return self._tool_params

    # ── 参数别名 ──

    def _parse_aliases(self):
        """从工具定义中提取参数别名。"""
        self._aliases = {}
        for name, tool_def in self._tools_section.get("builtin", {}).items():
            aliases = tool_def.get("aliases")
            if aliases:
                self._aliases[name] = aliases  # {canonical_name: [alt1, alt2, ...]}

    @property
    def aliases(self) -> dict:
        """参数别名字典（tool_name → {canonical: [alternatives]}）。"""
        return self._aliases

    def resolve_alias(self, tool_name, args):
        """解析参数别名，返回 (resolved_args, changed: bool)。

        例：read 工具的 file_path → path
        """
        tool_aliases = self._aliases.get(tool_name)
        if not tool_aliases:
            return args, False

        changed = False
        for canonical, alternatives in tool_aliases.items():
            if canonical not in args:
                for alt in alternatives:
                    if alt in args:
                        args[canonical] = args.pop(alt)
                        changed = True
                        break
        return args, changed

    # ── 自定义工具 ──

    def _parse_custom_tools(self):
        """从 YAML 生成 OpenAI function calling 格式的自定义工具列表。"""
        self._custom_tools = []
        for name, tool_def in self._tools_section.get("custom", {}).items():
            params = tool_def.get("parameters", {})
            schema = self._build_schema(params)
            self._custom_tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool_def.get("description", ""),
                    "parameters": schema,
                }
            })

    @property
    def custom_tools(self) -> list:
        """自定义工具列表（OpenAI function calling 格式）。"""
        return self._custom_tools

    # ── 策略查询 ──

    def get_policy(self, category, rule_name=None):
        """查询策略规则。

        Args:
            category: 策略类别（如 "tool_admission", "request_limits"）
            rule_name: 具体规则名（可选，不指定则返回整个类别）

        Returns:
            dict 或 None
        """
        policy = self._policies.get(category)
        if policy is None:
            return None
        if rule_name is None:
            return policy
        for rule in policy.get("rules", []):
            if rule.get("name") == rule_name:
                return rule
        return None

    def get_policy_value(self, category, rule_name, default=None):
        """获取策略规则的值。"""
        rule = self.get_policy(category, rule_name)
        if rule is None:
            return default
        return rule.get("value", default)

    # ── 浏览器约束 ──

    @property
    def valid_browser_profiles(self) -> set:
        """合法的浏览器 profile 集合。"""
        browser = self._tools_section.get("prefix_matched", {}).get("browser", {})
        constraints = browser.get("constraints", {})
        return set(constraints.get("valid_profiles", ["openclaw", "chrome"]))

    @property
    def default_browser_profile(self) -> str:
        """默认浏览器 profile。"""
        browser = self._tools_section.get("prefix_matched", {}).get("browser", {})
        constraints = browser.get("constraints", {})
        return constraints.get("default_profile", "openclaw")

    # ── 工具分类查询 ──

    def get_tools_by_category(self, category) -> list:
        """按类别查询工具。"""
        result = []
        for name, tool_def in self._tools_section.get("builtin", {}).items():
            if tool_def.get("category") == category:
                result.append(name)
        for name, tool_def in self._tools_section.get("custom", {}).items():
            if tool_def.get("category") == category:
                result.append(name)
        return result

    def get_tool_metadata(self, tool_name) -> dict:
        """获取工具的元数据（类别、描述、副作用等）。"""
        # 先查 builtin
        tool_def = self._tools_section.get("builtin", {}).get(tool_name)
        if tool_def:
            return {
                "name": tool_name,
                "type": "builtin",
                "category": tool_def.get("category"),
                "description": tool_def.get("description"),
                "side_effects": tool_def.get("side_effects", False),
                "resource_type": tool_def.get("resource_type"),
            }
        # 再查 custom
        tool_def = self._tools_section.get("custom", {}).get(tool_name)
        if tool_def:
            return {
                "name": tool_name,
                "type": "custom",
                "category": tool_def.get("category"),
                "description": tool_def.get("description"),
                "side_effects": tool_def.get("side_effects", False),
                "resource_type": tool_def.get("resource_type"),
                "executor": tool_def.get("executor"),
            }
        return {}

    def has_side_effects(self, tool_name) -> bool:
        """判断工具是否有副作用。"""
        meta = self.get_tool_metadata(tool_name)
        return meta.get("side_effects", False)

    # ── 验证接口 ──

    def validate_tool_args(self, tool_name, args):
        """验证工具调用参数是否符合本体约束。

        Returns:
            (is_valid: bool, errors: list[str])
        """
        errors = []
        params_def = self._get_tool_params_def(tool_name)
        if params_def is None:
            return True, []  # 未知工具不做验证

        # 检查必填参数
        for param_name, param_def in params_def.items():
            if param_def.get("required") and param_name not in args:
                errors.append(f"Missing required parameter: {param_name}")

        # 检查枚举值
        for param_name, value in args.items():
            if param_name in params_def:
                enum_values = params_def[param_name].get("enum")
                if enum_values and value not in enum_values:
                    errors.append(f"Invalid value for {param_name}: '{value}'. Must be one of: {enum_values}")

        # 检查额外参数
        allowed = set(params_def.keys())
        extra = set(args.keys()) - allowed
        if extra:
            errors.append(f"Unexpected parameters: {extra}")

        return len(errors) == 0, errors

    def _get_tool_params_def(self, tool_name):
        """获取工具的参数定义（原始 YAML）。"""
        # builtin
        tool_def = self._tools_section.get("builtin", {}).get(tool_name)
        if tool_def:
            return tool_def.get("parameters", {})
        # custom
        tool_def = self._tools_section.get("custom", {}).get(tool_name)
        if tool_def:
            return tool_def.get("parameters", {})
        # prefix matched
        for prefix, prefix_def in self._tools_section.get("prefix_matched", {}).items():
            for name, tdef in prefix_def.get("tools", {}).items():
                if name == tool_name:
                    return tdef.get("parameters", {})
        return None

    # ── 一致性检查（对比 proxy_filters.py 硬编码）──

    def check_consistency(self, hardcoded_allowed, hardcoded_schemas, hardcoded_params):
        """与 proxy_filters.py 中的硬编码数据对比，检测不一致。

        用于渐进迁移阶段的安全网。

        Returns:
            list[str]: 不一致项描述，空列表表示完全一致
        """
        issues = []

        # 对比白名单
        onto_allowed = self.allowed_tools
        if onto_allowed != hardcoded_allowed:
            only_onto = onto_allowed - hardcoded_allowed
            only_hard = hardcoded_allowed - onto_allowed
            if only_onto:
                issues.append(f"Only in ontology allowed_tools: {only_onto}")
            if only_hard:
                issues.append(f"Only in hardcoded allowed_tools: {only_hard}")

        # 对比 schema 覆盖的工具
        onto_schema_keys = set(self.clean_schemas.keys())
        hard_schema_keys = set(hardcoded_schemas.keys())
        schema_diff = onto_schema_keys.symmetric_difference(hard_schema_keys)
        if schema_diff:
            issues.append(f"Schema key mismatch: {schema_diff}")

        # 对比每个工具的合法参数
        for name in onto_allowed:
            onto_params = self.tool_params.get(name, set())
            hard_params = hardcoded_params.get(name, set())
            if onto_params != hard_params:
                issues.append(f"Tool '{name}' params mismatch: ontology={onto_params}, hardcoded={hard_params}")

        return issues

    # ── 报告 ──

    def summary(self) -> str:
        """返回本体摘要（可用于日志/调试）。"""
        lines = [
            f"Tool Ontology v{self._metadata.get('version', '?')}",
            f"  Builtin tools: {len(self._tools_section.get('builtin', {}))}",
            f"  Custom tools:  {len(self._tools_section.get('custom', {}))}",
            f"  Prefix groups: {len(self._tools_section.get('prefix_matched', {}))}",
            f"  Policies:      {len(self._policies)}",
            f"  Aliases:       {sum(len(v) for v in self._aliases.values())} mappings",
        ]
        return "\n".join(lines)

    def reload(self, path=None):
        """重新加载本体文件（支持热更新）。"""
        self._raw = _load_yaml(path or _ONTOLOGY_FILE)
        self._tools_section = self._raw.get("tools", {})
        self._policies = self._raw.get("policies", {})
        self._metadata = self._raw.get("metadata", {})
        self._parse()


# ---------------------------------------------------------------------------
# 模块级单例（懒加载）
# ---------------------------------------------------------------------------
_instance = None


def get_ontology(path=None) -> ToolOntology:
    """获取本体引擎单例。"""
    global _instance
    if _instance is None:
        _instance = ToolOntology(path=path)
    return _instance


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """命令行入口：显示本体摘要或执行验证。"""
    import argparse
    parser = argparse.ArgumentParser(description="Tool Ontology Engine")
    parser.add_argument("--summary", action="store_true", help="Show ontology summary")
    parser.add_argument("--tools", action="store_true", help="List all tools")
    parser.add_argument("--categories", action="store_true", help="List tools by category")
    parser.add_argument("--policies", action="store_true", help="Show all policies")
    parser.add_argument("--check", action="store_true", help="Check consistency with proxy_filters.py")
    parser.add_argument("--validate", nargs=2, metavar=("TOOL", "ARGS_JSON"),
                        help="Validate tool call args")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    onto = ToolOntology()

    if args.summary or (not any([args.tools, args.categories, args.policies, args.check, args.validate])):
        print(onto.summary())
        return

    if args.tools:
        all_tools = sorted(onto.allowed_tools | onto.custom_tool_names)
        if args.json:
            print(json.dumps([{"name": t, **onto.get_tool_metadata(t)} for t in all_tools], indent=2))
        else:
            for t in all_tools:
                meta = onto.get_tool_metadata(t)
                side = "⚠️" if meta.get("side_effects") else "  "
                print(f"  {side} {t:25s} [{meta.get('category', '?')}] {meta.get('description', '')[:60]}")
        return

    if args.categories:
        cats = {}
        for t in sorted(onto.allowed_tools | onto.custom_tool_names):
            meta = onto.get_tool_metadata(t)
            cat = meta.get("category", "unknown")
            cats.setdefault(cat, []).append(t)
        for cat, tools in sorted(cats.items()):
            print(f"\n  {cat}:")
            for t in tools:
                print(f"    - {t}")
        return

    if args.policies:
        for cat, policy in onto._policies.items():
            print(f"\n  {cat}: {policy.get('description', '')}")
            for rule in policy.get("rules", []):
                val = rule.get("value", "")
                val_str = f" = {val}" if val else ""
                print(f"    • {rule['name']}{val_str}")
                if rule.get("rationale"):
                    print(f"      ↳ {rule['rationale']}")
        return

    if args.check:
        # 导入 proxy_filters 的硬编码数据进行对比
        try:
            from proxy_filters import ALLOWED_TOOLS, CLEAN_SCHEMAS, TOOL_PARAMS
            issues = onto.check_consistency(ALLOWED_TOOLS, CLEAN_SCHEMAS, TOOL_PARAMS)
            if issues:
                print("⚠️  Consistency issues found:")
                for issue in issues:
                    print(f"  - {issue}")
                sys.exit(1)
            else:
                print("✅ Ontology and proxy_filters.py are consistent")
        except ImportError as e:
            print(f"Cannot import proxy_filters: {e}")
            sys.exit(1)
        return

    if args.validate:
        tool_name, args_json = args.validate
        try:
            tool_args = json.loads(args_json)
        except json.JSONDecodeError:
            print(f"Invalid JSON: {args_json}")
            sys.exit(1)
        is_valid, errors = onto.validate_tool_args(tool_name, tool_args)
        if is_valid:
            print(f"✅ {tool_name}({args_json}) is valid")
        else:
            print(f"❌ {tool_name}({args_json}) validation failed:")
            for e in errors:
                print(f"  - {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
