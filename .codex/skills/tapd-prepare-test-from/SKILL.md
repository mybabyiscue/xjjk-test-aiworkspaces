---
name: tapd-prepare-test-from
description: 基于已审批 TAPD 测试用例、代码审查证据、用户确认环境配置和真实数据来源，生成可追溯的 HTTP API 测试准备产物与执行计划。当前正式支持 HTTP API 和 MySQL 只读查询；其他协议或数据库类型必须明确分类或阻断，禁止伪装成已支持能力。
---

# TAPD 测试准备

本 skill 是五步测试流水线第四步。它只准备测试数据、证据映射、执行计划和文档，不同步 TAPD，不执行正式接口测试。自动 HTTP 或 SQL 写入只允许进入第五步防篡改执行计划，且必须由用户确认测试环境和受控写连接。

## 必读资源

执行前完整读取：

- [execution-workflow.md](references/execution-workflow.md)：命令顺序、可覆盖路径、PowerShell/Bash 示例和发布前校验。
- [assessment-contract.md](references/assessment-contract.md)：评估包、场景、证据、数据来源和动作契约。
- [configuration.md](references/configuration.md)：环境、凭证、数据库适配器和 Token Gate 配置。

## 硬性 Gate

任一 Gate 失败时立即停止，不生成最终文档，不做降级替代。

1. **输入 Gate**：校验 `output/test_cases.md`、`output/tapd_cases.json`、确认文件和代码审查证据存在且哈希一致。
2. **审批 Gate**：`output/latest/testcase_confirmation.json.approved` 必须为 `true`。
3. **API 环境 Gate**：读取工作区 `config/environments_config.json`，等待用户明确选择一个测试环境；禁止默认选择域名。
4. **数据库 Gate**：读取工作区 `config/connections.json`，只读查询必须使用 `enabled=true` 且 `access_mode=read-only` 的连接；受控写入必须另行确认 `controlled-write` 连接。
5. **Token Gate**：`healthcheck_url`、成功码、Token 失效 HTTP 状态或业务错误码必须来自环境配置；缺失时阻断，不猜路径、不猜登录控件。
6. **转换 Gate**：本 skill 必须调用 `scripts/generate_query_plan.py` 和 `scripts/generate_model_mapping.py`，从代码审查证据生成 `output/test_preparation/query_plan.json` 与 `output/test_preparation/model_mapping.json`；禁止要求用户预先手工放置这两个文件来通过存在性检查。
7. **证据 Gate**：接口 Method、Path、字段、表、列、断言、反向规则都必须关联结构化证据；证据文件、哈希和锚点必须可验证。
8. **生成物 Gate**：`query_plan.json` 与 `model_mapping.json` 必须符合 `references/generated-artifact-schema.json`，且 `generation_report.status` 必须为 `ready`；若证据不足，必须输出缺失原因并阻断，不得生成空壳、示例值或默认值顶上去。
9. **发布 Gate**：运行硬编码扫描，确认 skill 包不含旧业务残留、Python 编译缓存、本地配置或凭证。

## 必出物

- 优先使用用户确认的只读连接执行单条 `SELECT` 并复用现有真实记录；查不到时不得直接标记 `blocked`，应依次评估真实业务 API、受控 SQL 和人工创建。
- 正向用例只允许真实查询或真实创建的数据；禁止 Mock、Fake、Stub、Mock seed、占位主键和凭空构造字段。
- 仅为有明确需求或代码校验证据的反向用例构造无效值、边界值或越权 ID。
- 自动创建优先使用有源码证据的真实业务 API。无稳定 API 且不绕过被测行为时，允许用户单独确认的 `controlled-write` 测试连接执行显式列、参数化单条 `INSERT`；清理只允许按 `TEST_` 标识精确或前缀匹配的参数化单条 `DELETE`。
- 禁止 `UPDATE`、DDL、存储过程、`TRUNCATE`、无界 `DELETE`、文件导出、锁操作、注释 SQL 和生产环境写入。
- 每个自动创建动作必须有一一对应的清理动作；测试失败、Token 失效或部分准备失败时也必须反向清理。清理失败必须登记残留数据并返回失败。
- 将 API Token、账号和密码仅保存到已被 Git 忽略的 `config/environments_config.json`；数据库连接凭证仅保存到已被 Git 忽略的 `config/connections.json`。禁止写入技能目录、Markdown、JSON 中间产物、日志或 Git 跟踪文件。
- 文档中的敏感 Header 必须显示为 `***`。
- 将每条真实查询结果按 `库名:表名:【JSON】` 写入 `output/test_data_manifest.md`。

## 证据规则

- HTTP Method 只能来自控制器注解证据。
- 请求路径必须使用代码审查产物中的完整网关路径。
- DTO 字段、表名、列名和数据库断言只能来自代码审查与物理元数据。
- 每条用例必须恰好归入一个接口组或不可接口测试组。
- 缺少证据时将用例标记为 `blocked` 并说明缺失项；不得补写推测结论。
- 只有存在真实代码依赖证据时才生成核心集成流程，否则写明 `core_flow_blocker_reason`。

## 执行

严格执行 [execution-workflow.md](references/execution-workflow.md) 中的命令，不直接手写最终文档：

1. 校验确认文件并生成不可变输入快照。
2. 初始化评估壳。
3. 从已确认的表证据生成只读查询计划并执行真实查询。
4. 按“复用 -> 真实 API -> 受控 SQL -> 人工创建后复查”的顺序生成 `data_preparation` 和 `model_mapping.json`。新增功能只准备真实上游依赖，不预创建被测目标对象；更新、删除和状态流转用例可先创建真实目标对象。
5. 合并并校验 `preparation_assessment.json`。
6. 仅在校验报告 `valid` 为 `true` 时渲染最终文档。
7. 需要交给第五步执行时，生成唯一的 `output/test_execution/execution_plan.json`，写入 assessment SHA-256、用例哈希、代码复审批次以及结构化 setup/cleanup；第五步直接消费并按当前 assessment 规范重建比较，不得重新生成或修补计划。存在阻断项时不得执行接口。

## 最终产物

只在全部 Gate 和评估校验通过后生成：
Stage 4 必须由 skill 自己生成以下准备产物：

- `output/test_preparation/query_plan.json`：由 `scripts/generate_query_plan.py` 根据 `evidence_index.json`、接口证据、核心流程证据和表证据生成。
- `output/test_preparation/model_mapping.json`：由 `scripts/generate_model_mapping.py` 根据同一批已审查证据生成结构化接口、参数、断言、非接口用例和核心流程映射。
- `output/test_preparation/preparation_assessment.json`
- `output/interface_test_preparation.md`
- `output/non_interface_cases.md`
- `output/integration_test_flow.md`
- `output/test_data_manifest.md`

如果代码审查证据不足以生成前两个 JSON，流程必须停在转换 Gate，并说明缺少哪类证据以及需要补充什么；不得继续生成最终 Markdown 文档。

## 稳定安全约束

以下约束保留在代码中，因为它们是安全底线，不属于业务硬编码：

- 禁止生产环境写入。
- 禁止敏感信息进入日志、Markdown、中间 JSON、测试快照和异常文本。
- 禁止 Mock、Fake、Stub 或占位数据冒充真实业务数据。
- 禁止无界删除、DDL、TRUNCATE、UPDATE、存储过程、SQL 注释绕过、锁、睡眠函数和文件导出。
- 自动写入前必须有用户确认、隔离标识、结构化证据和可追踪清理策略。
- 缺少证据不得猜测。

## 动态内容来源

租户、业务主键、表名、字段名、接口路径、Method、请求字段、响应结构、响应码、业务状态、权限规则、边界规则和预期结果只能来自需求、代码审查证据或真实查询记录。

查询不到真实数据时，只能进入以下状态之一：

- 规划有源码证据的真实 API 创建。
- 规划用户确认的受控 SQL 创建。
- 请求人工创建并重新只读查询确认。
- 因证据或安全条件不足阻断。

## 当前能力边界

- 正式执行对象：HTTP API。
- 当前数据库适配器：MySQL 只读查询；其他数据库类型在能力检测阶段阻断。
- RPC、GraphQL、WebSocket、消息队列和异步消息消费类对象必须分类为不可由当前 HTTP 执行器直接执行，或在补充适配器后再进入计划。
