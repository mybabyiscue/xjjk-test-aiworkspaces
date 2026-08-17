# 输出契约与目录结构规范

## 1. 允许写入的根目录

本 skill 现在统一管理源码隔离缓存与测试用例证据审查的输出。允许写入的目录包括：

*   `output/code_sources/`：专用于管理多仓库隔离代码缓存与源元数据。
*   `output/code_review/`：专用于存放基于测试用例的证据分析、代码缺陷和 3 个定制化接口/表文档。
*   `output/latest/`：仅允许在用户“最终通过用例审批”后，写入用例门禁状态文件 `testcase_confirmation.json`。
*   `knowledge/`：仅允许在用例审批通过后，写入测试经验沉淀文件。

---

## 2. 详细的输出目录结构

重构后的总体输出目录如下：

```text
output/
├── code_sources/
│   ├── cache/
│   │   └── <repo_name>/                     # 多仓库隔离代码存放区
│   ├── runs/
│   │   └── <source_run_id>/
│   │       ├── source_manifest.json          # 代码拉取元数据清单
│   │       ├── code_source_confirmation.json  # 初始代码确认状态 (approved: false/true)
│   │       └── input_check.md                # 代码源输入核对
│   └── latest/                               # 最新版代码拉取元数据与确认状态
├── code_review/
│   ├── runs/
│   │   └── <review_run_id>/
│   │       ├── raw/
│   │       │   ├── source_inventory.json      # 仓库角色、变更和版本清单
│   │       │   ├── codegraph_queries/        # symbol/callers/callees/impact 查询记录
│   │       │   ├── gateway_discovery.json    # 自动网关与外部配置发现
│   │       │   ├── code_findings.json        # 统一代码质量 findings
│   │       │   ├── parsed_test_cases.json    # 解析出的测试用例数据
│   │       │   ├── prepare_findings.json      # 代码源初审结构化发现
│   │       │   ├── code_entry_index.json     # 服务端入口索引
│   │       │   ├── route_consistency.json   # 前后端网关路由一致性
│   │       │   ├── testcase_interface_evidence.json # 用例接口证据
│   │       │   ├── call_chain_evidence.json  # 调用链证据
│   │       │   ├── table_evidence.json       # 匹配库表数据
│   │       │   ├── table_resolution.json     # 表解析与确认状态
│   │       │   └── requirement_code_matrix.json # 需求、用例与代码实现矩阵
│   │       ├── review_context.json            # 输入 Hash、平台、网关与疑问决策
│   │       ├── code_source_confirmation.json  # 已批准代码源状态快照
│   │       ├── unit_test_interfaces.md       # 单元测试接口文档 [NEW]
│   │       ├── core_process_interfaces.md    # 核心流程接口文档 [NEW]
│   │       ├── table_information.md          # 表信息文档 [NEW]
│   │       ├── unresolved_tables.md           # 元数据未确认或冲突表
│   │       ├── code_prepare_findings.md        # 代码源初审发现
│   │       ├── requirement_implementation_review.md # 需求实现正确性审查
│   │       ├── requirement_findings.md          # 需求实现缺失、冲突与不可验证项
│   │       ├── requirement_review_status.json   # Halt 状态及用户处理决定
│   │       ├── testcase_evidence_summary.md   # 用例证据摘要
│   │       ├── code_review_report.md          # 综合代码审查报告
│   │       ├── evidence_index.json            # 批次与产物 Hash 索引
│   │       └── review_validation.json         # 发布及审批就绪状态
│   └── latest/                               # 仅由校验发布脚本生成的完整批次副本
└── latest/
    └── testcase_confirmation.json            # 测试用例最终审计确认门禁 [最终审批后]
```

---

## 3. 三大定制文档结构说明

### 3.1 单元测试接口文档 (`unit_test_interfaces.md`)
*   包含在测试代码目录中扫描到的接口、路由、请求类型，展示参数详细信息（位置、必填、Javadoc描述）以及单测 Mock 信息。
*   首个 Markdown 表必须严格使用以下表头；即使没有匹配数据，也必须保留表头和分隔行：

| 用例编号 | 方法签名 | 输入边界场景 | 需隔离的外部依赖 | 当前覆盖状态 | 代码位置 |
|---|---|---|---|---|---|

### 3.2 核心流程接口文档 (`core_process_interfaces.md`)
*   包含在正常生产 Controller 目录中提取的、与用例流程匹配的核心 API 接口。对于 `@RequestBody` 参数，递归展开其 DTO/实体类定义的字段类型与注释。
*   首个 Markdown 表必须严格使用以下表头；即使没有匹配数据，也必须保留表头和分隔行：

| 用例编号 | 接口名称/描述 | 接口类型与地址 | 请求参数 | 返回参数 | 调用链路 | 代码位置 |
|---|---|---|---|---|---|---|

### 3.3 表信息文档 (`table_information.md`)
*   包含与用例流程关联的物理库表。表名称、字段名、类型、是否为空、默认值和物理表注释，必须完全取自所选平台的 `/xjjk-yewu-sql` 元数据文档。
*   代码存在但元数据未唯一命中的表只能写入 `unresolved_tables.md`，不得写入本文件。
*   首个 Markdown 表必须严格使用以下表头；即使没有确认表，也必须保留表头和分隔行：

| 用例编号 | 所属平台 | 库.表名 | 物理注释 | 确认等级 | 判定依据说明 | 关键字段 | 读/写类型 | 租户隔离 |
|---|---|---|---|---|---|---|---|---|

### 3.4 生成、发布与审批校验

*   生成脚本必须从共享契约常量渲染以上三组表头，禁止各脚本独立维护同名字段。
*   发布校验和最终审批必须检查每份文档的首个 Markdown 表；缺表、字段缺失、字段改名、顺序变化或额外字段均视为契约不兼容并阻断。
*   此结构同时满足下游数据准备的最小字段要求：`unit_test_interfaces.md` 的“用例编号、方法签名、代码位置”，`core_process_interfaces.md` 的“用例编号、接口名称/描述、接口类型与地址、请求参数、代码位置”，以及 `table_information.md` 的“用例编号、库.表名、关键字段、读/写类型”。

---

## 4. 审批完成门禁契约 (`testcase_confirmation.json`)

仅在用户最终明确同意审批通过后，写入：

```json
{
  "approved": true,
  "approved_at": "2026-07-15T21:42:00+08:00",
  "testcase_hash": "<test_cases.md的SHA-256>",
  "code_review_run_id": "<review_run_id>"
}
```

禁止把 `latest/` 作为生成目录。必须先在 `runs/<review_run_id>/` 完整生成并校验，再发布整个目录。
