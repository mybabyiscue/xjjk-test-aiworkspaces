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
