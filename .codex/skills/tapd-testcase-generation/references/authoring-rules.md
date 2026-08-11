# Test Case Authoring And Output Contract

本文件是 `test_cases.md`、`questions.md`、`tapd_cases.json`、`test_cases.xlsx` 和 `review_history.md` 的唯一格式契约。`SKILL.md` 不重复定义字段结构。

## 1. `test_cases.md`

必须包含以下章节，顺序不可改变：

```markdown
# 测试用例

## 一、P0 核心用例
## 二、P1 重要分支和异常用例
## 三、P2 边界兼容和低频用例
```

每条用例使用连续且唯一的 `### TCxxx - [用例标题]` 标题。编号从 `TC001` 开始，不因优先级章节切换而重新编号。

每条用例必须使用以下完整字段和顺序：

```markdown
### TC001 - [模块层级] - [前置条件或操作场景] - [预期反馈]
- **用例名称**：[与标题中 TC 编号之后的文本完全一致]
- **用例目录**：[业务目录]
- **需求ID**：[TAPD short_id]
- **用例类型**：功能测试
- **用例状态**：正常
- **用例等级**：P0
- **所属端/角色/系统**：[客户端、角色或系统]
- **功能模块**：[单一功能模块]
- **前置条件**：[Given]
- **测试步骤**：
  1. [When 的第一步]
- **预期结果**：
  1. [Then 的可观察断言]
- **关联需求点**：[requirement.md 章节、功能点或 BDD 场景]
- **备注**：[无，或优先级上浮原因和必要风险说明]
```

约束：

- `用例类型` 只能是 `功能测试`、`性能测试`、`安全性测试`。
- `用例状态` 固定为 `正常`。
- `用例等级` 只能是 `P0`、`P1`、`P2`，并与所在章节一致。
- 每条用例只覆盖一个测试目的和一个 Given-When-Then 闭环。
- `测试步骤` 和 `预期结果` 必须是非空有序列表。
- 不使用“操作成功”“展示正常”“接口正常”等不可断言描述。
- 未被需求证据定义的 HTTP 状态码、提示文案、字段、表、锁、缓存、重试、降级或实现机制不得写入用例。

## 2. `questions.md`

始终生成该文件。格式固定为：

```markdown
# 待确认问题清单

| 编号 | 关联功能点/模块 | 问题类型 | 具体问题描述 | 来源 | 建议确认方 |
|---|---|---|---|---|---|
| Q001 | [功能点] | 需求疑问点转移 | [问题] | requirement.md 第十四章 | 产品经理 |
```

问题类型只能是：

- `需求疑问点转移`
- `质量门禁保守策略`
- `自检未通过`

没有问题时保留标题和表头，并增加：

```markdown
| 无 | 无 | 无 | 无待确认问题 | 无 | 无 |
```

本技能不得在该文件中擅自把问题标记为已销号。销号由用户确认或下游代码证据审查完成。

## 3. `tapd_cases.json`

JSON 根节点和用例对象只使用以下字段：

```json
{
  "story": {
    "workspace_id": "47387910",
    "id": "1147387910001063060",
    "short_id": "1063060",
    "name": "原始需求标题"
  },
  "total_count": 1,
  "cases": [
    {
      "case_id": "TC001",
      "title": "[模块层级] - [场景] - [反馈]",
      "directory": "业务目录",
      "requirement_id": "1063060",
      "case_type": "功能测试",
      "case_status": "正常",
      "priority": "P0",
      "system_scope": "所属端/角色/系统",
      "module": "功能模块",
      "precondition": "Given",
      "steps": ["When"],
      "expected_results": ["Then"],
      "requirement_points": ["requirement.md 第十三章 场景一"],
      "remarks": "无"
    }
  ]
}
```

约束：

- `story.workspace_id`、`story.id`、`story.short_id`、`story.name` 必须来自真实需求来源，不得为空或使用占位值。
- `total_count` 必须等于 `cases` 数量。
- 所有字符串必填且去除首尾空白。
- `steps`、`expected_results`、`requirement_points` 必须是非空字符串数组。
- `case_type`、`case_status`、`priority` 使用第1节规定的枚举值。
- JSON 与 Markdown 的用例编号、数量、标题顺序、目录、需求ID、类型、状态、优先级和标题完全一致。

## 4. `test_cases.xlsx`

工作簿固定输出到 `output/test_cases.xlsx`，并满足以下契约：

- 工作簿只包含一个名为 `测试用例` 的工作表。
- 第一行为表头，一条测试用例占一行，行顺序与 `tapd_cases.json.cases` 完全一致。
- 冻结首行并启用自动筛选；所有数据单元格启用自动换行和顶端对齐。
- 不合并数据单元格，不增加契约之外的列，不在工作簿内维护独立业务规则。

表头及顺序固定为：

| 列序号 | 表头 | JSON 来源 |
|---|---|---|
| 1 | 用例编号 | `case_id` |
| 2 | 用例名称 | `title` |
| 3 | 用例目录 | `directory` |
| 4 | 需求ID | `requirement_id` |
| 5 | 用例类型 | `case_type` |
| 6 | 用例状态 | `case_status` |
| 7 | 用例等级 | `priority` |
| 8 | 所属端/角色/系统 | `system_scope` |
| 9 | 功能模块 | `module` |
| 10 | 前置条件 | `precondition` |
| 11 | 测试步骤 | `steps` |
| 12 | 预期结果 | `expected_results` |
| 13 | 关联需求点 | `requirement_points` |
| 14 | 备注 | `remarks` |

`steps`、`expected_results` 和 `requirement_points` 使用 `1. 内容` 格式在单元格内换行。Excel 必须由最新的 `tapd_cases.json` 全量生成，不得从 Markdown 反向解析或在旧工作簿上局部修改。

Excel 与 JSON 的用例数量、编号、顺序和全部字段必须一致。任何差异均视为产物校验失败。

## 5. `review_history.md`

用户要求修订时按轮次追加，不覆盖历史：

```markdown
## 第 N 轮评审反馈（YYYY-MM-DD）

| 被拒绝用例ID | 拒绝原因 | 期望修改方向 |
|---|---|---|
| TC003 | [具体原因] | [明确方向] |
```

拒绝原因或修改方向不明确时，先向用户确认，不写模糊记录。

技能内部确定性校验失败后的自动修正不新增评审轮次。用户提出新的修订意见时才追加新一轮记录。

## 6. 移交

产物通过校验后移交 `tapd-code-source-review`。本技能不得生成或修改 `output/latest/testcase_confirmation.json`，不得更新知识库经验条目，不得同步 TAPD。


## 7. Coverage report

Generate output/testcase_coverage.md with function-point coverage, BDD coverage, uncovered items, and atomicity checks. Report completion only after the audit passes.


## 8. Stable coverage references

Use stable identifiers from `output/coverage_manifest.json` in `requirement_points`. Do not encode business keywords or rely on display names as coverage keys.
# Atomic test-point contract

Every testcase is one executable Given-When-Then loop and references exactly one `TP-xxx` test point. Build `output/testpoint_matrix.json` before final artifacts. Keep one stable `atomic_rule_id` per independently verifiable rule, preserve exact prompts/tab order/dynamic text from `requirement.md`, and transfer unresolved rules to `questions.md` instead of inventing assertions. Reject vague expected results and duplicate/equivalent cases during audit.
