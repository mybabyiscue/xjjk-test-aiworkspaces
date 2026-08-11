# 评估数据契约

模型只生成结构化映射；脚本负责合并、校验、渲染和生成执行计划。示例中的值都是结构示例，不得复制为实际业务值。

## 查询计划

`query_plan.json`：

```json
{
  "connection": "用户确认的只读连接名",
  "queries": [
    {
      "query_reference": "QRY_DOMAIN_RESOURCE",
      "database": "EVIDENCE_DATABASE",
      "table": "EVIDENCE_TABLE",
      "purpose": "说明关联用例和取值目的",
      "max_rows": 20,
      "sql": "SELECT explicit_column FROM evidence_database.evidence_table WHERE evidenced_filter = %s LIMIT 20"
    }
  ]
}
```

SQL 必须是无注释、无多语句、带 `LIMIT` 的单条 `SELECT`，禁止 `SELECT *`。库名、表名、列名必须来自表结构证据。

## 结构化证据

所有接口、字段、表、列、断言、反向规则和清理依据都使用同一证据结构：

```json
{
  "source_type": "code | requirement | table_metadata",
  "source_file": "unit_test_interfaces.md",
  "source_location": "operation_anchor",
  "source_sha256": "证据文件当前 sha256",
  "rule_type": "method | path | field | assertion | data | cleanup",
  "rule_summary": "一句话说明证据证明了什么",
  "anchor": "operation_anchor"
}
```

校验器会确认文件存在、哈希匹配，并检查 `source_location`、`anchor`、`method_name`、`field_name` 或行号能在证据文件中找到。文档示例不得作为业务证据。

## 模型映射

`model_mapping.json` 根结构：

```json
{
  "data_preparation": {"entries": []},
  "interface_cases": [],
  "non_interface_cases": [],
  "core_flows": [],
  "core_flow_blocker_reason": "没有核心流程时填写证据不足原因；存在流程时为空字符串"
}
```

## 接口用例组

```json
{
  "interface_key": "stable_operation_key",
  "interface_evidence": {
    "protocol": "http",
    "service": "evidence_service",
    "operation": "operation_anchor",
    "method": "POST",
    "path": "/evidence/resource",
    "response_type": "json",
    "evidence_references": []
  },
  "covered_case_keys": ["case_generated_from_real_case_id"],
  "request_variants": [
    {
      "name": "原始用例标题",
      "variant_type": "negative",
      "scenario_category": "permission",
      "scenario_tags": ["permission", "tenant-isolation"],
      "evidence_references": [],
      "case_keys": ["case_generated_from_real_case_id"],
      "headers": {"X-Session": "***"},
      "auth_header_name": "X-Session",
      "query": {},
      "parameters": [
        {
          "name": "resourceId",
          "location": "path",
          "type": "string",
          "required": true,
          "value": "REAL_QUERY_VALUE",
          "source": {
            "kind": "database",
            "reference": "QRY_DOMAIN_RESOURCE.resourceId",
            "resolver": "copy"
          },
          "query_reference": "QRY_DOMAIN_RESOURCE"
        }
      ],
      "request_body": null,
      "expected": {
        "http_status": 403,
        "response_assertions": [
          {
            "assertion_type": "json_path",
            "path": "$.result",
            "operator": "exists",
            "evidence_reference": {}
          }
        ],
        "database_assertions": []
      },
      "setup_steps": [],
      "cleanup_steps": []
    }
  ],
  "negative_variant_policy": "covered",
  "negative_variant_evidence": [],
  "audit": {
    "status": "可审核",
    "evidence_status": "接口、用例、真实数据和断言已绑定",
    "reason": "说明证据链",
    "reviewer": "Codex",
    "reviewed_at": "实际 ISO-8601 时间"
  }
}
```

`variant_type` 只表达执行语义，且只能是 `positive` 或 `negative`；`scenario_category` 直接保留业务分类原值；`scenario_tags` 用于补充标签。接口组可以只有边界、权限、状态、幂等或其他证据驱动场景，场景分类与执行语义不再共用同一个字段。

## 参数来源

参数来源使用可扩展结构：

```json
{
  "source": {
    "kind": "database | upstream_response | setup_response | environment_config | protocol_constant | dynamic_unique | current_time | manual_preparation | negative_constructed | unresolved",
    "reference": "可追溯来源",
    "resolver": "取值方式"
  }
}
```

当前未实现或不能自动解析的来源必须阻断，不得静默使用默认值。`unresolved` 不能进入正式执行计划。

## 数据准备动作

`strategy` 允许 `reuse`、`api_create`、`sql_insert`、`manual_create`。禁止 Mock、Fake、Stub 和占位主键。

自动创建动作必须具备：

- `cleanup_policy`：`automatic`、`idempotent`、`not_required_with_evidence`、`manual` 或 `blocked_unsafe`。
- `depends_on`：显式依赖关系，清理排序不依赖数组倒序。
- 结构化 `evidence_reference`。
- `manifest`：库、表和记录摘要；不得包含凭证。

`sql_insert` 必须是显式列、单条参数化 `INSERT`；`sql_delete` 必须是带限定条件的单条参数化 `DELETE`。清理影响 0 行只有 `idempotent` 策略允许。

## 核心流程

核心流程必须有至少两个步骤、结构化证据和显式参数依赖。`parameter_dependencies.target` 支持 `path`、`query`、`header`、`cookie`、`body`。不存在真实调用依赖证据时保持 `core_flows` 为空，并填写 `core_flow_blocker_reason`。
