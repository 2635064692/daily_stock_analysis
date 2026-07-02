# 设计原则

## Provider 模式

所有外部能力接入遵循统一接口抽象：

- **搜索**：`BaseSearchProvider` → `_do_search()`，由 `SearchService` 统一编排
- **行情**：`data_provider/` 多源 fallback 链
- **通知**：`src/notification_sender/` 多渠道发送器

规则：
- 新增 Provider 需继承对应基类，实现核心方法，其余（Key 轮询、重试、错误记录）由基类提供
- Provider 之间无相互依赖，各自独立可用
- 生产级 Provider 必须编写对应 offline mock 测试

## 搜索 Provider 优先级

搜索 Provider 注册顺序决定 fallback 链的优先级。设计约束：

- **Grok 优先**：LLM 结构化返回，广度+深度搜索策略，结果质量最高
- **中文优先**：A 股场景下中文搜索效果好的 Provider 排前
- **免费额度优先**：配额充足的 Provider 优先于配额紧张的
- **兜底保障**：SearXNG 自建/公共实例是最后的匿名兜底

## 多 Key 负载均衡

每个 Provider 支持多个 API Key，轮询负载均衡 + 错误隔离：

- 正常请求：`itertools.cycle` 轮询
- 单个 Key 连续错误 ≥3 次：自动跳过，由其他 Key 接管
- 全部 Key 均错误时：重置计数器并重试

## 并行搜索策略

`search_comprehensive_intel` 将 6 个搜索维度（最新消息、机构分析、风险排查、公司公告、业绩预期、行业分析）分配到不同的 Provider，维度间并行执行：

- 分配策略：固定轮询 (`dim_index % len(providers)`)
- 隔离：单个维度失败不影响其他维度
- 时间窗口：按 search profile 自适应调整 (ultra_short/short/medium/long)

## 配置管理

- 所有运行时配置从 `.env` 读取，`Config` dataclass 统一解析
- LLM 渠道按 `LLM_<NAME>_*` 前缀约定组织，支持多模型/多 Provider 声明
- 外部集成（AlphaSift）通过内存临时注入环境变量，不回写 `.env`

## 容错与降级

- 数据源 fallback：Tushare → Akshare → Efinance → Baostock → Yfinance
- 搜索 fallback：Provider 链式尝试，单个 Provider 失败自动切换
- LLM fallback：LiteLLM 多模型路由，主模型不可用自动降级
- 模式降级：非关键功能（AlphaSift、通知）可单独关闭，不影响核心分析链路
