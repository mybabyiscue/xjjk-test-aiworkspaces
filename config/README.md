# Local Configuration

该目录是本独立工作区的唯一配置入口：

- `credentials.local.json`：TAPD 和其他非 API 环境外部系统凭证。
- `environments_config.json`：API 环境、接口域名、账号、密码、Token 及数据驱动的 `token_probe` 配置。
- `connections.json`：`xjjk-yewu-sql` 使用的数据库连接登记。

所有真实配置文件均已加入 `.gitignore`，不得提交到版本库。

API 环境凭证直接保存在对应环境对象中，不再通过 `credentials_ref` 跳转到 `credentials.local.json`。`authorization` 按原值使用，不自动增删认证方案前缀。
