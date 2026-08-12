# 输入门禁规则

## 必要输入

执行本技能前，必须能够读取以下文件：

- `output/test_cases.md`
- `output/requirement.md`
- `output/questions.md`
- `.codex/skills/xjjk-yewu-sql/state/documents/metadata_document.json`
- `output/code_sources/latest/source_manifest.json`
- `output/code_sources/latest/code_source_confirmation.json`

## 必要确认状态

必须同时满足：

- `code_source_confirmation.json.approved = true`
- 审批中的 `manifest_sha256` 与当前 `source_manifest.json` 一致。
- 每个代码源的 `fetch_status = success`、`codegraph_status = healthy`，Git 分支与 Commit 不为空且不为 `HEAD`。

测试用例的最终确认发生在本技能完成代码证据复查之后，因此 `testcase_confirmation.json` 不是本技能的输入门禁。代码源确认状态不是 `true` 时，必须立即停止。

当代码源未通过确认时，必须明确提示：

```text
代码源准备阶段尚未人工确认，禁止进入测试用例代码证据分析。
```

## 服务路径检查

对 `source_manifest.json` 中每个 `fetch_status = success` 的服务，必须检查：

- `cache_path` 是否存在。
- 路径是否可读。
- 是否至少能识别出一个可扫描目录。

如果服务获取失败或缓存路径不可读，不允许静默跳过，必须记入 `input_check.md` 和后续索引文件。

## 网关证据与路由一致性 Gate

网关前缀和证据首先由 `discover_gateway_evidence.py` 自动发现。用户不负责手工检索文件或行号。仅当发现结果为 `external_config_required`、多个高置信候选冲突或无有效证据时，才进入人工确认。

- 网关证据行必须解析出唯一完整 `Path`、`url`、`uri` 或 `route` 路径；只移除末尾 `/*` 或 `/**` 后，必须与输入前缀规范化精确相等。
- 普通字符串包含关系不能证明网关前缀。无法解析、解析出多个候选或不一致时，状态为 `gateway_evidence_unresolved` 并阻断。
- 服务端 Controller 路由必须与扫描到的前端消费者按规范化路径段匹配。前端候选前缀与当前生效前缀不一致时为 `gateway_route_conflict`，多个不同候选时为 `ambiguous_gateway_route`，无消费者时为 `unverified`。
- `gateway_route_conflict`、`ambiguous_gateway_route`、`gateway_evidence_unresolved` 不得发布或最终审批。

## 门禁失败输出

门禁失败时必须输出 `input_check.md`，至少包含：

- 检查时间。
- 失败阶段。
- 缺失项或失败项。
- 原始异常或阻塞信息。
- 建议处理方式。

## 不允许的绕过方式

- 不允许手工伪造 `code_source_confirmation.json.approved = true` 继续执行。
- 不允许通过预先写入 `testcase_confirmation.json.approved = true` 跳过本技能的最终人工审批。
- 不允许在缺少 `output/test_cases.md` 时仅靠 `tapd_cases.json` 继续执行。
- 不允许把 `output/code_review/latest/` 作为 `--run-dir`。
- 不允许在缺少 `source_manifest.json` 时直接扫描缓存目录。
- 不允许跳过 `AGENTS.md` 读取。
- 绝不允许在审计与分析阶段以任何理由对业务源码文件（包括 Java, Vue, JS, TS, XML, SQL 等）进行任何的写入、覆写或修改操作。发现代码或注释等任何层面的不一致，必须作为缺陷（Findings）在报告中记录，绝不允许直接在代码中做出订正。

## 需求实现审查 Halt Gate

需求实现矩阵中出现 `partially_implemented`、`not_implemented`、`implementation_conflict` 或 `unverifiable` 时必须停止发布。只有用户明确选择 `resolved` 或 `ignored` 并提供非空说明，才能继续校验；该决定必须写入 `requirement_review_status.json`。

禁止把接口路由命中、关键词命中或调用链存在直接等同于需求完整实现。

