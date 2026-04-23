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

# 确保能导入父目录的模块（proxy_filters 等）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# PyYAML: config_loader 已依赖，确认可用
try:
    import yaml
except ImportError:
    yaml = None

# ---------------------------------------------------------------------------
# 本体文件路径
# ---------------------------------------------------------------------------
_ONTOLOGY_DIR = os.path.dirname(os.path.abspath(__file__))
_ONTOLOGY_FILE = os.path.join(_ONTOLOGY_DIR, "tool_ontology.yaml")
_DOMAIN_ONTOLOGY_FILE = os.path.join(_ONTOLOGY_DIR, "domain_ontology.yaml")
_POLICY_ONTOLOGY_FILE = os.path.join(_ONTOLOGY_DIR, "policy_ontology.yaml")


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
            legacy = tool_def.get("legacy_params", [])
            if params is not None and len(params) > 0:
                schema = self._build_schema(params)
                self._clean_schemas[name] = schema
                # TOOL_PARAMS 包含 schema params + legacy params
                self._tool_params[name] = set(params.keys()) | set(legacy)
            elif params is not None and len(params) == 0:
                # 显式空参数（如 agents_list）
                self._clean_schemas[name] = {"type": "object", "properties": {}, "additionalProperties": False}
                self._tool_params[name] = set(legacy) if legacy else set()
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

    # ── 语义查询（POC：从枚举到推理的跳跃）──

    def query_tools(self, **filters) -> list:
        """基于语义属性查询工具 — 这是本体论的核心价值。

        不列工具名，而是用属性描述"我要什么样的工具"。
        新增的工具只要声明了正确的属性，就自动被规则覆盖。

        支持的 filters：
            side_effects: bool     — 是否有副作用
            category: str          — 工具类别
            resource_type: str     — 操作的资源类型
            tool_type: str         — "builtin" / "custom"

        返回匹配的工具名列表（排序）。

        示例：
            onto.query_tools(side_effects=True)
            → ['cron', 'data_clean', 'edit', 'exec', 'message', ...]

            onto.query_tools(category="file_operation")
            → ['edit', 'read', 'write']

            onto.query_tools(side_effects=False, resource_type="WebPage")
            → ['web_fetch', 'web_search']
        """
        results = []
        all_tools = self._get_all_tool_defs()
        for name, tool_def, tool_type in all_tools:
            if self._matches_filters(tool_def, tool_type, filters):
                results.append(name)
        return sorted(results)

    def _get_all_tool_defs(self):
        """返回所有工具的 (name, def_dict, type) 三元组。"""
        tools = []
        for name, tool_def in self._tools_section.get("builtin", {}).items():
            tools.append((name, tool_def, "builtin"))
        for name, tool_def in self._tools_section.get("custom", {}).items():
            tools.append((name, tool_def, "custom"))
        return tools

    def _matches_filters(self, tool_def, tool_type, filters):
        """检查工具定义是否匹配所有 filter 条件。"""
        for key, value in filters.items():
            if key == "side_effects":
                if tool_def.get("side_effects", False) != value:
                    return False
            elif key == "category":
                if tool_def.get("category") != value:
                    return False
            elif key == "resource_type":
                if tool_def.get("resource_type") != value:
                    return False
            elif key == "tool_type":
                if tool_type != value:
                    return False
        return True

    def infer_policy_targets(self, policy_condition: str) -> list:
        """从语义条件推导受影响的工具 — 本体推理的核心能力。

        不需要人手动维护工具列表，本体从属性自动推导。

        Args:
            policy_condition: 语义条件表达式，支持：
                "side_effects == true"
                "side_effects == false"
                "category == <value>"
                "resource_type == <value>"
                "side_effects == true AND category == <value>"

        Returns:
            匹配的工具名列表（排序）

        示例：
            # 传统方式：手动列出 8 个工具
            NIGHT_BLOCKED = {"write", "edit", "exec", "cron", "message", ...}

            # 本体方式：语义条件自动推导
            onto.infer_policy_targets("side_effects == true")
            → 自动找到所有有副作用的工具，新增工具自动覆盖
        """
        filters = self._parse_condition(policy_condition)
        return self.query_tools(**filters)

    def _parse_condition(self, condition: str) -> dict:
        """解析简单的语义条件表达式为 filter dict。"""
        filters = {}
        # 支持 AND 组合
        parts = [p.strip() for p in condition.split("AND")]
        for part in parts:
            part = part.strip()
            if "==" in part:
                key, value = [x.strip() for x in part.split("==", 1)]
                # 处理布尔值
                if value.lower() == "true":
                    value = True
                elif value.lower() == "false":
                    value = False
                filters[key] = value
        return filters

    # ── 语义分类（从观察到决策的桥梁）──

    def classify_tool_call(self, tool_name: str) -> dict:
        """对工具调用进行语义分类 — 从存在推导本质。

        不是查表（"这个工具名在列表里吗"），
        而是推理（"这个工具是什么类别、有什么副作用、操作什么资源"）。

        用于 proxy_filters 的语义观察模式：
        - 每次工具调用时，ontology 提供语义元数据
        - proxy 记录这些信号，为未来的语义策略决策积累数据

        Returns:
            {
                "name": "write",
                "known": True,           # ontology 认识这个工具吗
                "category": "file_operation",
                "side_effects": True,
                "resource_type": "File",
                "risk_level": "high",    # 推理出的风险等级
                "policy_tags": ["night_blockable", "audit_required"],
            }
        """
        meta = self.get_tool_metadata(tool_name)
        if not meta:
            return {
                "name": tool_name,
                "known": False,
                "category": None,
                "side_effects": None,
                "resource_type": None,
                "risk_level": "unknown",
                "policy_tags": ["unknown_tool"],
            }

        side_fx = meta.get("side_effects", False)
        category = meta.get("category")

        # 从属性推理风险等级和策略标签
        risk = "low"
        tags = []

        if side_fx:
            risk = "high"
            tags.append("night_blockable")
            tags.append("audit_required")
        else:
            tags.append("audit_exempt")

        if category == "file_operation" and side_fx:
            tags.append("approval_required")
        if meta.get("resource_type") == "WebPage":
            tags.append("rate_limited")
            if risk == "low":
                risk = "medium"

        return {
            "name": tool_name,
            "known": True,
            "category": category,
            "side_effects": side_fx,
            "resource_type": meta.get("resource_type"),
            "risk_level": risk,
            "policy_tags": sorted(tags),
        }

    # ── Phase 1: 生成 proxy_filters 兼容数据 ──

    def generate_proxy_data(self):
        """生成与 proxy_filters.py 硬编码完全相同格式的数据结构。

        Phase 1 核心方法：证明本体可以生成 drop-in 替换数据。
        返回 dict，key 与 proxy_filters.py 中的全局变量一一对应。

        Returns:
            {
                "ALLOWED_TOOLS": set,
                "ALLOWED_PREFIXES": list,
                "CLEAN_SCHEMAS": dict,
                "TOOL_PARAMS": dict,
                "CUSTOM_TOOLS": list,
                "CUSTOM_TOOL_NAMES": set,
                "VALID_BROWSER_PROFILES": set,
            }
        """
        return {
            "ALLOWED_TOOLS": self.allowed_tools,
            "ALLOWED_PREFIXES": self.allowed_prefixes,
            "CLEAN_SCHEMAS": self.clean_schemas,
            "TOOL_PARAMS": self.tool_params,
            "CUSTOM_TOOLS": self.custom_tools,
            "CUSTOM_TOOL_NAMES": self.custom_tool_names,
            "VALID_BROWSER_PROFILES": self.valid_browser_profiles,
        }

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


# ===========================================================================
# Phase 4 P1: 领域本体 + 策略引擎 纯函数 API (V37.9.12)
# ---------------------------------------------------------------------------
# 三个纯函数，为 Phase 4 wiring 提供最小可用接口：
#
#   load_domain_ontology(path=None) -> dict
#       加载 domain_ontology.yaml。纯函数，无副作用。
#
#   find_by_domain(domain_name, ontology=None, path=None) -> list
#       返回给定域的概念列表。Actor/Resource/Task/Memory 有 instance 列表；
#       Provider.types 是字符串列表，自动包成 {"id": ...}；Tool 以
#       source_of_truth=tool_ontology.yaml 为权威源，此处返回 []，调用方
#       应改用 ToolOntology。
#
#   evaluate_policy(policy_id, context=None, policy_data=None, path=None) -> dict
#       评估策略：返回结构化结果含 limit / hard_limit / type / applicable 等。
#       Phase 4 P1 只覆盖 static 策略；temporal/contextual 先返回 applicable=None
#       并标 reason="needs_context_evaluator"，留给 Phase 4 P2 完善。
#
# 设计原则:
#   - 纯函数 (输入决定输出，零全局状态)
#   - 可 inject 数据 (policy_data/ontology 参数供测试使用)
#   - 失败不抛: 找不到文件/id 返回 found=False 的结构化结果
#   - 向后兼容: 不动 ToolOntology / get_ontology / get_policy
# ===========================================================================

import re as _re


def load_domain_ontology(path=None) -> dict:
    """加载 domain_ontology.yaml，返回解析后的 dict。

    Args:
        path: 可选的文件路径（默认 ontology/domain_ontology.yaml）。

    Returns:
        dict: 完整的 YAML 解析结果。失败抛 IOError/ImportError。

    纯函数：每次调用重新读盘，无缓存。调用方若需高频访问应自行缓存。
    """
    return _load_yaml(path or _DOMAIN_ONTOLOGY_FILE)


def load_policy_ontology(path=None) -> dict:
    """加载 policy_ontology.yaml，返回解析后的 dict。"""
    return _load_yaml(path or _POLICY_ONTOLOGY_FILE)


# 六域 → 该域内概念实例所在的 YAML 键（用于 find_by_domain 归一化）
_DOMAIN_CONCEPT_KEYS = {
    "Actor": "instances",     # list of {id, kind, ...}
    "Resource": "categories",  # list of {id, storage, ...}
    "Task": "taxonomy",        # list of {id, actors, ...}
    "Memory": "layers",        # list of {id, backend, ...}
    "Provider": "types",       # list of strings → 包装成 {id: s}
    # Tool: 无直接 instance 列表; source_of_truth=tool_ontology.yaml
    # 调用方应用 ToolOntology.query_tools() 查询。
}


def find_by_domain(domain_name, ontology=None, path=None) -> list:
    """返回指定域的概念实例列表（归一化为 [{"id": ..., ...}, ...]）。

    Args:
        domain_name: 六域之一 (Actor / Tool / Resource / Task / Provider / Memory)
        ontology: 预加载的 domain_ontology dict（可选，测试注入用）
        path: 可选的文件路径

    Returns:
        list of dict。每项至少含 "id"。
        - 未知域 → []
        - Tool → [] (请用 ToolOntology.query_tools())
        - Provider.types 字符串列表 → [{"id": "llm"}, {"id": "vl_model"}, ...]
        - 其他域的 instance/category/taxonomy/layer 原样返回

    纯函数：不修改输入，不写任何文件。
    """
    data = ontology if ontology is not None else load_domain_ontology(path)
    domains = (data or {}).get("domains") or {}
    dom = domains.get(domain_name)
    if not dom:
        return []
    key = _DOMAIN_CONCEPT_KEYS.get(domain_name)
    if key is None:
        # 显式声明 Tool 走 source_of_truth
        return []
    raw = dom.get(key) or []
    # Provider.types 是字符串列表 — 归一化
    normalized = []
    for item in raw:
        if isinstance(item, dict):
            normalized.append(item)
        elif isinstance(item, str):
            normalized.append({"id": item})
    return normalized


def _parse_limit_from_rule(rule_text):
    """从规则文本中提取数值阈值（仅当 YAML 未显式声明 `limit` 时回退使用）。

    支持模式:
        "... ≤ N ..." / "... <= N ..."
        "... < N ..."
        "... N 以内" (中文)
    返回 int 或 None。
    """
    if not rule_text or not isinstance(rule_text, str):
        return None
    # 优先匹配 ≤ / <= / < 后的整数（允许下划线作千位分隔）
    m = _re.search(r"(?:≤|<=|<)\s*([\d_]+)", rule_text)
    if m:
        try:
            return int(m.group(1).replace("_", ""))
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# V37.9.13 Phase 4 P2: context evaluators for temporal/contextual policies
# ---------------------------------------------------------------------------
# Each evaluator returns (applicable: Optional[bool], reason: Optional[str]).
# applicable=None means "undecidable with given context" (reason explains why).
# Unregistered policies stay at reason="no_context_evaluator_registered" —
# P2 rolls out matchers incrementally, not big-bang.
# ---------------------------------------------------------------------------

def _eval_quiet_hours(policy, context):
    """temporal: hour_of_day ∈ [0, 7) → applicable."""
    hour = context.get("hour")
    if hour is None and context.get("now") is not None:
        try:
            hour = context["now"].hour
        except AttributeError:
            return None, "context_now_must_have_hour_attr"
    if hour is None:
        return None, "context_missing_hour"
    try:
        h = int(hour)
    except (ValueError, TypeError):
        return None, "context_hour_invalid_type"
    if h < 0 or h > 23:
        return None, "context_hour_out_of_range"
    return (0 <= h < 7), None


def _eval_task_match(policy, context, expected_task):
    task = context.get("task")
    if task is None:
        return None, "context_missing_task"
    return (task == expected_task), None


def _eval_has_alert(policy, context):
    """contextual: messages contain [SYSTEM_ALERT] marker → applicable."""
    messages = context.get("messages")
    if messages is None:
        return None, "context_missing_messages"
    if not isinstance(messages, list):
        return None, "context_messages_must_be_list"
    for m in messages:
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        if isinstance(c, str):
            if "[SYSTEM_ALERT]" in c:
                return True, None
        elif isinstance(c, list):
            for block in c:
                if isinstance(block, dict):
                    txt = block.get("text", "")
                    if isinstance(txt, str) and "[SYSTEM_ALERT]" in txt:
                        return True, None
    return False, None


def _eval_has_image(policy, context):
    """contextual: has_image flag OR messages contain image blocks."""
    if "has_image" in context:
        return bool(context["has_image"]), None
    messages = context.get("messages")
    if messages is None:
        return None, "context_missing_has_image_or_messages"
    if not isinstance(messages, list):
        return None, "context_messages_must_be_list"
    for m in messages:
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        if isinstance(c, list):
            for block in c:
                if isinstance(block, dict) and block.get("type") in ("image_url", "image"):
                    return True, None
    return False, None


def _eval_need_fallback(policy, context):
    """contextual: primary failed or required capability missing."""
    if "need_fallback" in context:
        return bool(context["need_fallback"]), None
    return None, "context_missing_need_fallback"


_DATA_CLEAN_KEYWORDS = (
    "数据清洗", "清洗数据",
    "data clean", "data_clean", "clean data",
    "去重", "去空格", "规范化日期",
)


def _eval_data_clean_keywords(policy, context):
    """contextual: user_text contains data cleaning keywords."""
    text = context.get("user_text")
    if text is None:
        return None, "context_missing_user_text"
    if not isinstance(text, str):
        return None, "context_user_text_must_be_str"
    low = text.lower()
    for kw in _DATA_CLEAN_KEYWORDS:
        if kw in text or kw in low:
            return True, None
    return False, None


# Policy id → evaluator mapping (P2 staged rollout).
_CONTEXT_EVALUATORS = {
    # temporal
    "quiet-hours-00-07": _eval_quiet_hours,
    "dream-map-budget": lambda p, c: _eval_task_match(p, c, "kb_dream"),
    # contextual
    "alert-context-isolation": _eval_has_alert,
    "multimodal-routing": _eval_has_image,
    "fallback-chain-capability": _eval_need_fallback,
    "data-clean-tool-injection": _eval_data_clean_keywords,
}


def evaluate_policy(policy_id, context=None, policy_data=None, path=None) -> dict:
    """评估策略，返回结构化结果。

    Args:
        policy_id: 策略标识符 (如 "max-tools-per-agent")
        context: 运行时上下文 dict (可选；temporal/contextual 策略将读此参数，
                 Phase 4 P1 仅对 static 策略已完备)
        policy_data: 预加载的 policy_ontology dict (测试注入用)
        path: 可选的文件路径

    Returns:
        dict with stable keys:
            policy_id: str                           # 输入的 id
            found: bool                              # 是否在 YAML 中找到
            type: Optional[str]                      # static/temporal/contextual
            hard_limit: bool                         # 来自 YAML hard_limit 字段
            limit: Optional[int|float]               # 优先 YAML `limit` 字段，
                                                      # 次选 rule 文本 regex 解析
            applicable: Optional[bool]               # static=True;
                                                      # temporal/contextual=None (待 P2)
            rule: Optional[str]                      # 原始 rule 文本
            rationale: Optional[str]
            enforcement_site: Optional[str]
            governance_invariant: Optional[str]
            scope: Optional[list]
            reason: Optional[str]                    # 解释为何 found=False 或 limit=None

    纯函数：零全局状态修改，可并行调用。
    """
    # 统一空壳返回结构 — 任何分支都保证 key 齐全
    empty_result = {
        "policy_id": policy_id,
        "found": False,
        "type": None,
        "hard_limit": False,
        "limit": None,
        "applicable": None,
        "rule": None,
        "rationale": None,
        "enforcement_site": None,
        "governance_invariant": None,
        "scope": None,
        "reason": None,
    }

    try:
        data = policy_data if policy_data is not None else load_policy_ontology(path)
    except (IOError, OSError, ImportError) as e:
        result = dict(empty_result)
        result["reason"] = f"load_failed: {type(e).__name__}"
        return result

    policies = (data or {}).get("policies") or []
    match = None
    for pol in policies:
        if isinstance(pol, dict) and pol.get("id") == policy_id:
            match = pol
            break

    if match is None:
        result = dict(empty_result)
        result["reason"] = "policy_id_not_found"
        return result

    # 抽取数值阈值: 优先 explicit `limit` > `value` > rule 文本解析
    limit = match.get("limit")
    if limit is None:
        limit = match.get("value")
    if limit is None:
        limit = _parse_limit_from_rule(match.get("rule"))

    ptype = match.get("type")

    # Phase 4 P2: resolve applicability via context evaluator for non-static types.
    if ptype == "static":
        applicable = True
        reason = None
    elif context is None:
        applicable = None
        reason = "needs_context_evaluator"
    else:
        evaluator = _CONTEXT_EVALUATORS.get(policy_id)
        if evaluator is None:
            applicable = None
            reason = "no_context_evaluator_registered"
        else:
            try:
                applicable, reason = evaluator(match, context)
            except Exception as _err:
                applicable = None
                reason = f"evaluator_error: {type(_err).__name__}"

    result = {
        "policy_id": policy_id,
        "found": True,
        "type": ptype,
        "hard_limit": bool(match.get("hard_limit", False)),
        "limit": limit,
        "applicable": applicable,
        "rule": match.get("rule"),
        "rationale": match.get("rationale"),
        "enforcement_site": match.get("enforcement_site"),
        "governance_invariant": match.get("governance_invariant"),
        "scope": match.get("scope"),
        "reason": reason,
    }
    return result


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
