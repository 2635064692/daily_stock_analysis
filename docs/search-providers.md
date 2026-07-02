# 搜索 Provider 架构

## 概述

搜索服务 (`src/search_service.py`) 提供统一的新闻/情报搜索接口，支持 8 种搜索引擎的接入和编排。所有 Provider 继承 `BaseSearchProvider`，由 `SearchService` 统一管理优先级、fallback、缓存和多维搜索编排。

## Provider 列表

| Provider | 类名 | 接入方式 | 特点 |
| --- | --- | --- | --- |
| Anspire | `AnspireSearchProvider` | API Key | 中文内容优化，实时搜索 |
| Grok | `GrokSearchProvider` | OpenAI 兼容 API | LLM 驱动的广度+深度搜索，JSON 结构化返回 |
| Bocha | `BochaSearchProvider` | API Key | 中文搜索优化，内置 AI 摘要 |
| Tavily | `TavilySearchProvider` | API Key | AI 优化搜索，免费额度 |
| Brave | `BraveSearchProvider` | API Key | 隐私优先，美股优化 |
| SerpAPI | `SerpAPISearchProvider` | API Key | 多搜索引擎聚合 |
| MiniMax | `MiniMaxSearchProvider` | API Key | Coding Plan Web Search |
| SearXNG | `SearXNGSearchProvider` | 自建/公共实例 | 无配额匿名兜底 |

## Provider 基类契约

```python
class BaseSearchProvider(ABC):
    name: str            # Provider 标识名
    is_available: bool   # 是否有可用 API Key

    @abstractmethod
    def _do_search(self, query, api_key, max_results, days) -> SearchResponse: ...

    def search(self, query, max_results=5, days=7) -> SearchResponse: ...
```

基类自动提供：多 Key 轮询、错误计数与隔离 (>3 次自动跳过)、Transient 网络错误重试 (3 次指数退避)。

## Grok Provider 详解

### 架构

Grok 通过 xAI 的 OpenAI 兼容 `/chat/completions` 端点接入，利用 `stream=True` 的 SSE 流式调用获取完整搜索回答。

### 搜索策略

Grok 的 System Prompt 定义了独特的搜索策略：

1. **广度优先 (Breadth-First)**：从 5+ 个不同角度展开并行搜索
2. **深度挖掘 (Depth-First)**：在广度的基础上选择 2+ 个关键角度深入
3. **证据驱动 (Evidence-Based)**：每条结论必须附带权威来源链接
4. **结构化输出**：直接返回 JSON 数组，无前缀/后缀/包装

### 输出格式

```json
[
  {
    "title": "结论标题（≤30 字）",
    "content": "完整论述正文：核心结论 → 术语解释 → 类比降维 → 数据佐证",
    "sourceUrl": "https://...",
    "publishedDate": "YYYY-MM-DD"
  }
]
```

关键约束：

- `sourceUrl` 为必填字段，无来源的条目在解析阶段自动丢弃
- `content` 禁止内嵌 URL，来源统一由 `sourceUrl` 承载
- 无来源链接的条目会被 `_parse_json_results` 跳过（`if not source_url: continue`）

### 配置

```env
GROK_API_KEYS=key1,key2        # 支持多 key 逗号分隔
GROK_BASE_URL=https://api.x.ai/v1   # 默认 xAI 官方端点；支持任意 OpenAI 兼容端点
GROK_MODEL=grok-4.20-fast      # 默认模型
```

### 错误处理

| 场景 | 行为 |
| --- | --- |
| 空响应 | 自动重试 (最多 3 次) |
| 网络/SSE 解析异常 | 自动重试 (最多 3 次) |
| JSON 解析失败 | 尝试正则提取 `[...]`，失败返回空结果 |
| API Key 连续错误 ≥3 次 | 自动跳过该 Key，切换到其他 Key |

## 搜索模式

### 个股新闻搜索 (`search_stock_news`)

顺序 fallback 模式：按 Provider 注册优先级逐一尝试，首个返回有效结果的 Provider 获胜。

终止条件：

1. **直接命中**：识别到个股直接新闻（标题/摘要命中股票代码/名称）→ 立即返回
2. **中文优先**：A 股/中文场景优先选择中文结果
3. **全链 fallback**：所有 Provider 尝试完毕后返回最优排名结果

### 多维度情报搜索 (`search_comprehensive_intel`)

固定 Provider 分配 + 维度间并行：

- 6 个搜索维度（最新消息、机构分析、风险排查、公司公告、业绩预期、行业分析）
- 每个维度分配固定 Provider（`dim_index % len(providers)`）
- 维度间通过 `ThreadPoolExecutor` 并行执行
- 单维度失败不影响其他维度

搜索维度按市场自适应调整：

| 维度 | A 股关键词 | 港股/美股关键词 |
| --- | --- | --- |
| 最新消息 | 中文 最新/新闻/重大 | English latest news events |
| 机构分析 | 研报/目标价/评级 | analyst rating target price |
| 风险排查 | 减持/处罚/违规/利空 | risk insider selling litigation |
| 公司公告 | 公司公告/上交所/深交所 | (合并到 latest_news) |
| 业绩预期 | 业绩预告/财报/净利润 | earnings revenue profit growth |
| 行业分析 | 行业/竞争对手/市场份额 | industry competitors market share |

### 股票事件搜索 (`search_stock_events`)

针对特定事件类型（年报预告、减持公告等）的专项搜索，仍采用顺序 Provider fallback。

## 结果后处理管线

所有 Provider 的原始结果经过统一后处理管线：

1. **时效过滤** (`_filter_news_response`)：按 `news_window_days` 过滤过期结果
2. **语言重排** (`_prioritize_news_language`)：中文场景将中文结果排在前面
3. **相关性排序** (`_rank_news_response`)：直接个股命中的结果优先
4. **准入过滤** (`_filter_ranked_news_for_context`)：无源链接、无时效的结果淘汰
5. **数量裁剪** (`_limit_search_response`)：按 `max_results` 裁剪

## 缓存

- 内存缓存 (`self._cache`)，TTL 默认 600s (10 分钟)
- 缓存 Key：`(stock_code, query_hash)` 组合
- Inflight 锁：并发相同查询的后续请求会等待首个请求完成并复用结果

## 测试

- `tests/test_search_grok_provider.py`：Grok Provider 解析与重试行为单元测试（offline mock）
- `tests/test_search_intel_parallel.py`：多维搜索并行化行为测试（offline mock）

所有 Provider 测试均使用 mock requests，不依赖真实网络。
