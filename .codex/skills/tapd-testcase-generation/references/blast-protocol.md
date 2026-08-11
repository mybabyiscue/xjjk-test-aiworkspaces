# BLAST Execution Protocol

工作区没有提供 BLAST 缩写各字母的权威释义。本协议不推断或扩写缩写，只把 BLAST 作为以下证据约束测试设计流程的名称。获得团队正式定义后，应以正式定义替换本说明并保持下列真实性门禁。

## 1. 证据边界

- 只使用 `requirement.md` 原文、BDD 验收标准和用户已确认结论。
- 经验库只能提示检查角度，不能证明业务规则存在。
- 没有证据的测试设想进入 `questions.md`，不得形成带具体断言的用例。

## 2. 测试点矩阵

每个原子测试点记录：

| 字段 | 内容 |
|---|---|
| 测试点ID | `TP001` 起连续编号 |
| 需求功能点 | `requirement.md` 中的功能点 |
| 证据来源 | 章节、原文规则或 BDD 场景 |
| 活跃层级 | UI / API业务逻辑 / 三方集成 / 数据迁移 / 回归 / 安全隔离 / 观测性 |
| 场景维度 | Happy Path / Alternative/Error / Edge Cases / Compatibility |
| Given | 可验证前置状态 |
| When | 单一触发动作 |
| Then | 客观可观察结果 |
| 优先级 | P0 / P1 / P2 |
| 处置 | 生成用例 / 转入疑问 |

测试点与测试用例必须一一对应。转入疑问的测试点不生成用例。

## 3. 层级激活

- 需求明确页面、组件或交互变化时激活 UI。
- 需求明确服务端规则、状态流转、幂等或计算逻辑时激活 API/业务逻辑。
- 需求明确第三方或下游依赖时激活三方集成。
- 需求明确建表、字段或迁移要求时激活数据迁移；不得推测物理字段。
- 需求明确影响公共组件或存量流程时激活回归。
- 需求明确资金、权限、租户或敏感数据时激活安全隔离。
- 需求明确日志、埋点、监控或审计要求时激活观测性。

未满足激活条件的层级不得为追求覆盖率而生成用例。

## 4. 场景维度

- Happy Path：需求明确的主要成功路径。
- Alternative/Error：需求明确的异常、拒绝或替代路径。
- Edge Cases：需求明确的边界值、临界状态和特殊条件。
- Compatibility：需求明确的存量兼容、跨端或上下游影响。

需求没有定义的并发、超时、重试、降级、缓存、日志或审计行为只能进入疑问清单。

## 5. 覆盖完成条件

- 每个已确认功能点至少映射一个测试点，或明确记录无法覆盖的原因。
- 每个测试点包含证据来源和完整 Given-When-Then。
- 每个测试点只属于一个主要场景维度和一个主要测试目的。
- 所有疑问范围均已隔离，未进入断言用例。
- 覆盖率按已确认功能点计算，不把待确认功能点计为已覆盖。


## 6. Coverage audit

After the BLAST matrix, verify direct coverage for every core function point and BDD scenario. Verify explicit rejection, empty, repeat, first/subsequent, and combination rules have atomic cases. Coverage is not testcase count.

