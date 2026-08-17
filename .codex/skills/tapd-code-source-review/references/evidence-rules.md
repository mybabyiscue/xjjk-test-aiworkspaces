# 代码证据与增量规则

## 测试用例解析要求

必须从待最终确认的测试用例中尽量解析以下字段：

- 用例编号
- 优先级
- 所属端/角色/系统
- 功能模块
- 用例名称或测试点
- 前置条件
- 测试步骤
- 测试数据
- 预期结果
- 关联需求点
- 备注或风险点

必须兼容：

- Agent2 当前块状 Markdown
- 表格 Markdown

## 运行模式判断

判断依据至少包括：

- 是否存在上一次 `output/code_review/latest/evidence_index.json`
- 当前代码源 URL 是否与上次一致
- 当前 commit 或包 hash 是否与上次一致
- `test_cases.md` 内容是否变化

运行模式含义：

- `first_review`：不存在历史 review 结果
- `incremental_review`：代码或包版本变化，但输入测试用例整体稳定
- `full_review`：无法准确判断差异，或测试用例变化较大
- `no_code_change_review`：代码未变化，仅复用历史证据并输出说明

## 代码证据类型

扫描时必须按以下证据类型归类：

- `api_route`
- `service_method`
- `database_access`
- `sql_table`
- `entity_table`
- `external_call`
- `config_reference`

## 扫描对象

必须优先扫描：

- Controller 或路由文件
- Service
- Mapper、DAO、Repository
- SQL、XML、注解 SQL
- Entity、Model
- Feign、RPC、HTTP Client
- 配置项名称

## 数据库表提取规则

表来源包括：

- SQL 中出现的表
- Mapper XML 中出现的表
- Entity 注解中的表
- DAO、Repository 命名推断的表
- 注释或常量中明确出现的表名
- 利用 `codegraph` 对变更代码向下做 `callees` 追踪，定位调用到的持久化接口与 Mapper 方法以精准提取物理表名。

元数据自动对齐：
- 提取出物理表名后，必须只读反查 `xjjk-yewu-sql` 技能目录下的 `state/documents/metadata_document.json`（若存在）。
- 从中自动提取该表所属的真实物理库名（Schema）以及数据库物理表注释（Table Comment）。
- 表展现形式规范统一输出为：`库名.表名`，同时在报告中明示物理表注释。

提取限制与噪音过滤：
- **排除非代码文件**：在检索数据库表和接口路由时，必须跳过 `.md`、`.txt`、`.json`、`.properties`、`.yml`、`.yaml`、`.conf`、`.ini` 等非代码/非SQL文档和配置文件，避免将文档描述文字误判为代码符号。
- **排除 SQL 关键字与噪音**：自动过滤长度小于等于 2 的字符以及纯数字。且提取的表名必须排除 SQL 语法关键字和常见元数据属性（如 `replace`、`current_timestamp`、`source`、`value`、`select`、`where` 等）。

Javadoc 一致性校验：
- 比对对应 Java 实体类的头部 Class Javadoc 注释与真实的物理表注释。
- 如果两者除“Entity”、“Entity实体类”等修饰词外存在字面差异（例如拷贝了其他实体的 Javadoc 注释未作修改），必须判定为 `P2 级代码规范缺陷：Javadoc 注释与数据库表物理注释不一致（疑似模板拷贝残留）`。
- 该缺陷必须计入 Findings，严禁擅自修改业务代码文件。

表分组：

- 高置信表：有明确代码证据并能关联测试用例
- 候选表：有部分命中但证据不足
- 未确认表：疑似相关但缺少上下文


## 接口提取规则

接口来源包括：

- Controller 路由
- Swagger/OpenAPI 注解
- Feign Client
- RPC Client
- HTTP 调用代码
- 利用 `codegraph` 对修改函数或符号向上执行 `callers` 追踪与 `impact` 影响范围分析，直达最外层的路由 Controller，定位受影响的入站接口。

接口提取限制与噪音过滤：
- **排除非代码文件**：与表提取一致，必须跳过 `.md`、`.txt`、`.json`、`.properties`、`.yml`、`.yaml`、`.conf`、`.ini` 等非代码文件。
- **排除普通函数调用干扰（重点）**：通过非注解匹配（如匹配 `get('s')` / `get('id')`）提取接口时，必须进行有效路由路径校验。接口路由路径必须**以 `/` 开头**或**包含 `/` 符号**，或者是合法的 `http://` / `https://` 完整 URL。纯字母且无斜杠的单单词（如 `"s"`, `"id"`, `"status"`, `"type"`, `"name"`, `"key"`）一律作为普通属性获取函数调用过滤，不计入接口路由。

接口分组：

- 入站接口
- 出站接口或外部依赖
- 候选接口

无真实路由的用例只能通过业务域词与入口证据关联。仅命中“保存”“更新”等通用动作词时不得建立用例映射；必须同时命中 SKU、订单、活动等当前用例中可追溯的业务域词，防止把同模块内所有通用 CRUD 入口纳入证据。

## 修改摘要规则

如果能获取 Git diff：

- 输出明确变更文件
- 输出新增、修改、删除的关键类、方法、SQL

如果无法获取 diff：

- 只能输出“疑似相关实现点”
- 不得宣称为确定修改
