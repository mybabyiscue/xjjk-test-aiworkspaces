# 用例代码证据契约

## 证据链

每条已确认映射必须满足：

```text
测试用例 → 消费者调用（可选）→ 真实业务入口 → Service/领域方法 → 持久化或外部依赖
```

## 真实入口

允许：

- Spring/Web HTTP Mapping
- Feign、Dubbo、gRPC、GraphQL Resolver
- RocketMQ、Kafka、RabbitMQ Consumer
- Scheduled、XXL-Job、Quartz

前端 URL 常量和请求函数只能作为消费者证据，不能单独认定为服务端入口。

网关证据必须解析为单一、可复核的规范化路径前缀。证据文件、证据行、原始证据行及其 SHA-256 必须写入 `review_context.json`，并在证据生成、发布和最终审批前重新校验。前端消费者只能用于路由一致性校验，不能替代网关配置证据。

路由一致性结果写入 `raw/route_consistency.json`，状态为 `matched`、`gateway_route_conflict`、`ambiguous_gateway_route`、`gateway_evidence_unresolved` 或 `unverified`。

## 接口结构

- 合并类级和方法级路由。
- 展开 Body、Query、Path、Header 参数。
- 递归解析 DTO；记录字段类型、必填校验和注释。
- 展开响应包装对象和响应 DTO。
- 源码真实无参时才能输出“无参数”。

## 单元测试目标

- 下钻到最小业务规则方法。
- 记录正常、异常、边界场景。
- 输出需隔离的 DB、缓存、MQ 和外部服务，不强制指定 Mock。
- 通过现有测试文件和方法调用判断覆盖状态。

关键词仅允许召回候选。最终证据必须由符号、路由、调用关系或持久化引用确认。
