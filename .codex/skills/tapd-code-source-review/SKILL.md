---
name: tapd-code-source-review
description: 拉取并人工确认 TAPD 需求相关代码源，使用 CodeGraph、源码注解和指定 MySQL 平台元数据生成可追溯的接口、调用链、数据表及代码审查证据。用于已有 output/test_cases.md、requirement.md 和 questions.md，需要执行代码源门禁、平台绑定、疑问销号、证据审查、最终审批或知识沉淀的场景。
---

# TAPD Code Source Review

## 核心原则

- 只使用真实源码、CodeGraph 和用户确认平台的元数据作为证据。
- 测试用例已包含真实路由时按路由根强过滤；需求阶段未提供路由时，按业务词召回并仅保留达到既有评分门槛且绑定到用例的入口。
- 所有中间结果写入带 ID 的 `runs/` 目录；禁止把 `latest/` 当作工作目录。
- 网关证据必须进行路径前缀精确解析和证据文件/行哈希校验；禁止使用普通字符串包含判断。
- 必须生成 `raw/route_consistency.json`，校验 Controller 完整路由与前端真实消费者路径的一致性。
- 任一代码源失败、CodeGraph 不健康、代码源未审批、平台未确认、疑问未处理或元数据缺失时立即失败。
- HTTP Method 只来自服务端框架注解。表字段只来自指定连接的元数据精确匹配。
- 不生成 Mock 数据或推测字段，不修改业务源码。

执行前阅读以下契约：

- [输入门禁](references/gate-rules.md)
- [代码获取](references/fetch-rules.md)
- [初步代码审查](references/review-rules.md)
- [证据契约](references/evidence-contract.md)
- [证据与增量规则](references/evidence-rules.md)
- [表解析规则](references/table-resolution-rules.md)
- [测试数据安全](references/test-data-safety-rules.md)
- [输出契约](references/output-contract.md)

## 必要输入

- 用户提供的一个或多个 `<HTTPS Git URL>#<branch>`、仓库根 URL 配 `--branch`、CNB/GitLab 风格 `/tree/<branch>` 网页 URL，或 HTTPS ZIP URL。
- `output/requirement.md`
- `output/questions.md`
- `output/test_cases.md`
- `.codex/skills/xjjk-yewu-sql/state/documents/metadata_document.json`
- 可用的 CodeGraph CLI 及已启用的 MCP 注册。

## 唯一执行流程

所有命令从工作区根目录执行。

### 1. 创建代码源批次

```powershell
python .codex/skills/tapd-code-source-review/scripts/preflight_check.py `
  --code-url "https://git.example/service.git#feature-branch" `
  --test-cases output/test_cases.md `
  --requirement output/requirement.md `
  --questions output/questions.md `
  --metadata-document .codex/skills/xjjk-yewu-sql/state/documents/metadata_document.json `
  --output-root output/code_sources
```

仓库根地址也可通过 `--branch feature-branch` 指定分支；CNB/GitLab 地址如 `https://cnb.cool/group/repo/-/tree/feature-branch` 或 `/tree/feature-branch` 会自动归一化为 Git 仓库地址。

记录命令输出的 `<source_run_dir>`，后续步骤必须显式使用该目录。

### 2. 拉取、索引和初审

```powershell
python .codex/skills/tapd-code-source-review/scripts/fetch_code_sources.py `
  --manifest <source_run_dir>/source_manifest.json `
  --output-root output/code_sources

python .codex/skills/tapd-code-source-review/scripts/scan_prepare_findings.py `
  --manifest <source_run_dir>/source_manifest.json
```

任一服务失败时停止。展示代码源、分支、Commit、CodeGraph 状态和初审发现，等待用户确认。

### 3. 审批代码源

仅在用户明确批准后执行：

```powershell
python .codex/skills/tapd-code-source-review/scripts/approve_code_source.py `
  --run-dir <source_run_dir> `
  --approver USER `
  --approval-note "用户确认代码源与初审结果"
```

### 4. 自动发现环境与确认剩余歧义

先执行自动发现：

```powershell
python .codex/skills/tapd-code-source-review/scripts/discover_gateway_evidence.py `
  --manifest <source_run_dir>/source_manifest.json
```

工具必须先扫描本地 Gateway 配置、Java DSL、Controller、前端消费者和 Nacos 导入信息，输出 `raw/gateway_discovery.json` 与 `gateway_discovery.md`。唯一且可校验的候选由工具自动带入 review run；外部配置缺失或候选冲突时才向用户确认。

向用户展示可用数据库连接、自动发现结果和服务角色。不得猜测平台或网关前缀。

每个服务都必须提供：

- `--platform service_alpha=<用户确认的平台>`
- `--gateway-prefix service_alpha=/gateway-a`
- `--gateway-evidence service_alpha=path/to/gateway.yml:42`

同一代码源包含多个独立网关服务时，服务级前缀使用 `/`，并按源码路径增加模块覆盖规则：

- `--gateway-prefix-rule service_alpha:module_alpha=/gateway-a/module-b`
- `--gateway-evidence-rule service_alpha:module_alpha=path/to/evidence:42`

规则按源码文件路径最长匹配，未命中时回退到服务级前缀。每条规则都必须有包含对应前缀的真实证据行。

如果 `questions.md` 有实际疑问，用户必须明确选择 `resolved` 或 `ignored` 并提供说明。

### 5. 创建审查批次

```powershell
python .codex/skills/tapd-code-source-review/scripts/prepare_review_run.py `
  --source-run-dir <source_run_dir> `
  --test-cases output/test_cases.md `
  --requirement output/requirement.md `
  --questions output/questions.md `
  --metadata-document .codex/skills/xjjk-yewu-sql/state/documents/metadata_document.json `
  --platform service_alpha="<用户确认的平台>" `
  --gateway-auto-discover `
  --questions-decision resolved `
  --questions-note "疑问已由代码证据闭环" `
  --output-root output/code_review
```

记录命令输出的 `<review_run_dir>`。

### 6. 生成接口、调用链与表证据底稿

```powershell
python .codex/skills/tapd-code-source-review/scripts/analyze_testcase_evidence.py `
  --run-dir <review_run_dir> `
  --manifest <review_run_dir>/source_manifest.json `
  --source-confirmation <review_run_dir>/code_source_confirmation.json `
  --test-cases output/test_cases.md `
  --metadata-document .codex/skills/xjjk-yewu-sql/state/documents/metadata_document.json `
  --policy .codex/skills/tapd-code-source-review/assets/review-policy.json
```

### 7. 基于需求审查实现正确性

需求实现矩阵必须由工具根据 requirement、test cases、变更代码、调用链和真实证据生成初稿。禁止使用只有任意字符串 evidence 的人工 assessment；人工只能修正结论或处理工具标记为不可确定的项。

逐条核对 `requirement.md` 的需求点、验收标准、测试用例与真实代码证据。审查结论只允许：

- `implemented`
- `partially_implemented`
- `not_implemented`
- `implementation_conflict`
- `unverifiable`

审查人先生成结构化 `<assessment.json>`，每条用例必须包含 `requirement_id`、`acceptance_criterion`、`case_id`、`status`、`rationale` 和真实源码 `evidence`：

```powershell
python .codex/skills/tapd-code-source-review/scripts/review_requirement_implementation.py `
  --run-dir <review_run_dir> `
  --assessment <assessment.json>
```

脚本生成：

- `raw/requirement_code_matrix.json`
- `requirement_implementation_review.md`
- `requirement_findings.md`
- `requirement_review_status.json`

任一结论不是 `implemented` 时必须立即 Halt，不得执行发布。展示问题并等待用户处理。只有用户明确决定 `resolved` 或 `ignored` 且说明原因后，才允许记录决策：

```powershell
python .codex/skills/tapd-code-source-review/scripts/resolve_requirement_review.py `
  --run-dir <review_run_dir> `
  --decision resolved `
  --note "用户确认的处理说明"
```

不得仅凭路由命中判定 `implemented`；必须检查业务规则、边界、异常路径和持久化结果是否与需求一致。

### 8. 校验并发布

```powershell
python .codex/skills/tapd-code-source-review/scripts/validate_publish_review.py `
  --run-dir <review_run_dir> `
  --test-cases output/test_cases.md `
  --output-root output/code_review
```

只有校验通过的完整批次才能发布到 `output/code_review/latest/`。

发布前必须确认 `gateway_route_conflict`、`ambiguous_gateway_route`、`gateway_evidence_unresolved` 均为 0；否则保留当前 `runs/<review_run_id>/`，写入失败的 `review_validation.json`，不得更新 `latest/`。

### 8. 最终审批

展示接口、表、未闭环问题和三个主要文档，等待用户明确批准。批准后执行：

```powershell
python .codex/skills/tapd-code-source-review/scripts/approve_testcase_review.py `
  --run-dir <review_run_dir> `
  --test-cases output/test_cases.md `
  --approver USER `
  --approval-note "用户批准用例与代码证据" `
  --confirmation-path output/latest/testcase_confirmation.json `
  --knowledge-root knowledge
```

最终审批前必须再次校验证据文件和证据行哈希，并确认路由阻断计数均为 0。不得绕过发布校验直接审批。

## 完成条件

- 代码源、分支、Commit、CodeGraph 和人工确认绑定到同一 source run。
- 平台、网关证据、疑问决策和输入哈希绑定到同一 review run。
- 接口方法、完整路由、DTO、调用链和表均有源码或元数据证据。
- 每条测试用例均有需求实现结论；非完整实现项均已触发 Halt 并绑定用户处理决定。
- `table_information.md` 只包含指定平台元数据唯一命中的表；未命中表进入 `unresolved_tables.md`。
- `review_validation.json` 与 `evidence_index.json` 中的哈希全部匹配。
- 用户最终批准后才生成 `testcase_confirmation.json` 和知识索引记录。
