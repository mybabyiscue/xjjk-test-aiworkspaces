# 本地配置契约

真实配置只从工作区 `config/` 读取。禁止复制到 skill 目录或 `output/`：

- `config/environments_config.json`：API 环境元数据，不保存凭证。
- `config/credentials.local.json`：账号、密码和本地 Token。
- `config/connections.json`：当前工作区数据库连接唯一注册表。

禁止读取其他 skill、用户目录或历史缓存中的同名文件作为替代。

## API 环境

结构示例，值均为占位：

```json
{
  "environments": [
    {
      "name": "example-test",
      "api_domain": "https://api.example.invalid",
      "environment_type": "test",
      "allow_test_data_mutation": true,
      "healthcheck_url": "https://api.example.invalid/evidence-health",
      "healthcheck_headers": {"X-System-Type": "example"},
      "healthcheck_success_code": "EXAMPLE_SUCCESS",
      "token_error_codes": ["EXAMPLE_TOKEN_EXPIRED"],
      "healthcheck_unauthorized_statuses": [401],
      "auth_header_name": "X-Session",
      "credentials_ref": "environments.example-test",
      "timeout_seconds": 10,
      "retry_attempts": 1,
      "login_controls": {
        "account_test_id": "login-account",
        "password_test_id": "login-password",
        "submit_test_id": "login-submit",
        "token_storage_key": "session-token"
      }
    }
  ]
}
```

要求：

- `api_domain` 缺失时阻断，禁止默认域名。
- `healthcheck_url` 必须是已知会校验鉴权且无业务副作用的具体端点，不能只填根地址，禁止猜测路径。
- `token_error_codes` 或兼容字段 `healthcheck_unauthorized_codes` 必须非空。
- 非敏感固定 Header 可以放入 `healthcheck_headers`；凭证类 Header 只能由运行时读取本地凭证后注入。
- 只有 `environment_type=test` 且 `allow_test_data_mutation=true` 才允许生成写入计划。
- 登录控件只能使用 Stable ID、Test ID 或 Accessibility ID；缺失时阻断，不猜页面文本。

Token Gate 通过不代表允许数据变更。只有 `environment_type` 严格为 `test` 且 `allow_test_data_mutation` 严格为 `true` 时，后续阶段才允许 HTTP 或受控 SQL 写入；字段缺失或值不匹配时必须阻断写入。

结构示例：

`token_probe` 是可选的数据驱动规则。存在时必须完整提供：

- `url`：已知会校验鉴权且无业务副作用的具体端点，禁止使用 `api_domain` 根地址。
- `headers`：有代码或接口契约证据的固定非敏感 Header；禁止 Authorization、Cookie 或其他凭证。
- `response_code_path`：应用响应码的 JSONPath；不需要应用码时使用空字符串。
- `success_codes`：允许继续的应用码；不使用应用码时使用空数组。
- `unauthorized_codes`：表示 Token 失效的应用码；不使用应用码时使用空数组。

代码只解释这些通用字段。任何具体业务码只能出现在被 Git 忽略的环境配置中，禁止写入 Skill、脚本或测试。

## Token Gate

`credentials_ref` 指向当前环境的本地对象。续期成功后只能原子更新对应环境的 `authorization` 字段，不得改动其他环境。所有日志、Markdown、中间 JSON、测试快照和异常信息必须脱敏。

## 数据库连接

结构示例，值均为占位：

```json
{
  "connections": [
    {
      "name": "example-readonly",
      "database_type": "mysql",
      "host": "LOCAL_ONLY",
      "port": 3307,
      "username": "LOCAL_ONLY",
      "password": "LOCAL_ONLY",
      "enabled": true,
      "access_mode": "read-only",
      "charset": "utf8mb4",
      "ssl": {"ca": "LOCAL_ONLY"},
      "connect_timeout": 10,
      "read_timeout": 30,
      "write_timeout": 30,
      "retry_attempts": 1,
      "retry_delay_seconds": 0
    },
    {
      "name": "example-controlled-write",
      "database_type": "mysql",
      "host": "LOCAL_ONLY",
      "port": 3307,
      "username": "LOCAL_ONLY",
      "password": "LOCAL_ONLY",
      "enabled": true,
      "access_mode": "controlled-write",
      "environment_name": "example-test",
      "allowed_databases": ["EVIDENCE_DATABASE"],
      "allowed_tables": ["EVIDENCE_TABLE"]
    }
  ]
}
```

当前脚本只实现 MySQL 适配器。其他 `database_type` 必须阻断。只读查询必须选择 `enabled=true` 且 `access_mode=read-only` 的连接。受控写连接必须声明 `access_mode=controlled-write`、绑定当前测试环境，并提供库表白名单。

SSL、字符集、超时和重试策略从连接配置读取；禁止默认关闭 SSL。
