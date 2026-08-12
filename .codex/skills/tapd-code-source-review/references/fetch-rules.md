# 代码获取规则

## 输入

必须由用户提供至少一个 HTTP/HTTPS 代码路径。

支持：

- Git HTTP/HTTPS URL 与显式分支，例如 `https://git.example.com/group/service.git#feature-branch`。
- 仓库根 URL 配合 `preflight_check.py --branch feature-branch`，以及 CNB/GitLab 风格 `/tree/<branch>` 或 `/-/tree/<branch>` 网页 URL；门禁会自动归一化为 `<repo>.git#<branch>`。
- ZIP 源码包 URL，例如 `https://example.com/service.zip`。

## URL 类型识别

- URL 以 `.git` 结尾，或路径中明显包含 Git 仓库语义时，按 Git 仓库处理。
- URL 以 `.zip` 结尾，或响应内容可识别为 ZIP 时，按 ZIP 源码包处理。
- 无法识别时，标记为 `unknown`，不得静默跳过。
- Git URL 缺少 `#branch` 时立即失败，不允许使用远端默认分支代替用户选择。

## 缓存规则

写入 `output/code_sources/cache/`：

- Git 仓库缓存到 `cache/repos/<url_and_branch_hash>/`，同一仓库的不同分支必须隔离。
- ZIP 包缓存到 `cache/archives/<url_hash>/`。

Git URL 首次执行 clone，后续执行 fetch。ZIP URL 优先复用缓存；如果检测到远端变化，再重新下载。

Git 与 ZIP 外部获取失败时最多执行 3 次。前两次失败必须记录包含操作名、请求参数、当前次数、最大次数和异常信息的结构化 Warning；最后一次失败必须抛出原始异常并将该代码源标记为失败。

## 服务编号

每个输入 URL 分配稳定服务编号：

- `service_001`
- `service_002`
- `service_003`

不要依赖服务名作为唯一目录名，因为服务名可能重复或无法解析。

## Manifest 字段

`source_manifest.json` 至少包含：

- `source_run_id`
- `previous_source_run_id`
- `created_at`
- `code_sources`
- `service_id`
- `input_url`
- `source_type`
- `resolved_name`
- `branch`
- `commit`
- `cache_path`
- `fetch_status`
- `error`
- `codegraph_status`
- `codegraph_action`

## 安全限制

只允许读取和拉取代码，不允许执行服务代码、启动服务、运行构建脚本或修改业务代码。
