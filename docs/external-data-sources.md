# 外部数据源全景

本文档记录项目中所有外部数据源的接入方式、配置项、适用市场与 fallback 关系。

## 一、行情数据源 (Data Provider)

行情数据源通过 `data_provider/` 的 `DataFetcherManager` 统一管理，按优先级链式 fallback。

### 行情数据源能获取的信息

#### 1. 实时行情 (`get_realtime_quote`)

返回 `UnifiedRealtimeQuote` 对象，包含以下字段：

| 字段 | 类型 | 说明 | 数据源覆盖 |
| --- | :---: | --- | :---: |
| `price` | float | 最新成交价 | 全部 |
| `change_pct` | float | 涨跌幅(%) | 全部 |
| `change_amount` | float | 涨跌额 | 全部 |
| `open_price` | float | 开盘价 | 全部 |
| `high` | float | 最高价 | 全部 |
| `low` | float | 最低价 | 全部 |
| `pre_close` | float | 昨收价 | 全部 |
| `volume` | int | 成交量(股) | 全部 |
| `amount` | float | 成交额(元) | 全部 |
| `volume_ratio` | float | 量比 | 腾讯/东财/Longbridge |
| `turnover_rate` | float | 换手率(%) | 腾讯/东财/Longbridge |
| `amplitude` | float | 振幅(%) | 腾讯/东财/Longbridge |
| `pe_ratio` | float | 市盈率(动态) | 东财/Longbridge/Finnhub |
| `pb_ratio` | float | 市净率 | 东财/Longbridge/Finnhub |
| `total_mv` | float | 总市值(元) | 东财/Longbridge/Finnhub |
| `circ_mv` | float | 流通市值(元) | 东财 |
| `change_60d` | float | 60日涨跌幅(%) | 东财 |
| `high_52w` | float | 52周最高价 | YFinance |
| `low_52w` | float | 52周最低价 | YFinance |

**字段补充机制**：主数据源返回的行情若缺失 `volume_ratio`/`turnover_rate`/`pe_ratio`/`pb_ratio`/`total_mv`/`circ_mv`/`amplitude` 等字段，管理器会自动从次选数据源补充。

#### 2. 日线历史数据 (`get_daily_data`)

返回标准化 DataFrame，包含以下列：

| 列名 | 类型 | 说明 |
| --- | :---: | --- |
| `date` | datetime | 交易日期 |
| `open` | float | 开盘价 |
| `high` | float | 最高价 |
| `low` | float | 最低价 |
| `close` | float | 收盘价 |
| `volume` | float | 成交量(股) |
| `amount` | float | 成交额(元) |
| `pct_chg` | float | 涨跌幅(%) |
| `ma5` | float | 5日均线（自动计算） |
| `ma10` | float | 10日均线（自动计算） |
| `ma20` | float | 20日均线（自动计算） |
| `volume_ratio` | float | 量比（自动计算） |

#### 3. 基本面上下文 (`get_fundamental_context`)

聚合多个维度，按 block 组织：

| Block | 字段 | 说明 | 数据源 |
| --- | --- | --- | --- |
| `valuation` | `pe_ttm` / `pb` / `ps_ttm` / `market_cap` / `circulating_market_cap` | 估值指标 | Akshare 适配器 |
| `growth` | `revenue_yoy` / `profit_yoy` / `eps_yoy` / `roe` | 成长性指标 | Akshare 适配器 |
| `earnings` | `revenue` / `net_profit` / `eps` / `gross_margin` / `net_margin` | 盈利指标 | Akshare 适配器 |
| `institution` | `holder_count` / `top10_holder_pct` / `fund_count` / `fund_share_pct` | 机构持仓 | Akshare 适配器（仅 A 股） |
| `capital_flow` | `northbound_flow` / `main_net_inflow` | 资金流向 | Akshare 适配器（仅 A 股） |
| `dragon_tiger` | `dates` / `buy_seats` / `sell_seats` / `net_amount` | 龙虎榜 | Akshare 适配器（仅 A 股） |
| `boards` | `name` / `code` / `type` | 所属板块 | Akshare 适配器（仅 A 股） |

> 港股/美股/日股/韩股仅支持 `valuation`/`growth`/`earnings` 三个 block，其余 block 返回 `not_supported`。

#### 4. 筹码分布 (`get_chip_distribution`)

返回 `ChipDistribution` 对象：

| 字段 | 说明 |
| --- | --- |
| `profit_ratio` | 获利比例(0-1) |
| `avg_cost` | 平均持仓成本 |
| `cost_90_low` / `cost_90_high` | 90%筹码成本区间 |
| `concentration_90` | 90%筹码集中度（越小越集中） |
| `cost_70_low` / `cost_70_high` | 70%筹码成本区间 |
| `concentration_70` | 70%筹码集中度 |

#### 5. 大盘行情 (`get_main_indices`)

| 字段 | 说明 |
| --- | --- |
| `code` | 指数代码 |
| `name` | 指数名称 |
| `current` | 当前点位 |
| `change` | 涨跌点数 |
| `change_pct` | 涨跌幅(%) |
| `volume` | 成交量 |
| `amount` | 成交额 |

#### 6. 市场统计 (`get_market_stats`)

| 字段 | 说明 |
| --- | --- |
| `up_count` | 上涨家数 |
| `down_count` | 下跌家数 |
| `flat_count` | 平盘家数 |
| `limit_up_count` | 涨停家数 |
| `limit_down_count` | 跌停家数 |
| `total_amount` | 两市成交额 |

#### 7. 板块/概念排名 (`get_sector_rankings` / `get_concept_rankings`)

返回领涨/领跌板块或概念列表，每个包含板块名称、涨跌幅等。

#### 8. 人气股榜 (`get_hot_stocks`)

返回市场人气股列表。

#### 9. 涨停池 (`get_limit_up_pool`)

返回涨停池/连板梯队数据。

#### 10. 股票名称 (`get_stock_name`)

返回股票中文/英文名称，支持缓存和批量查询。

#### 11. 股票列表 (`get_stock_list`)

返回 DataFrame，包含 `code`（股票代码）和 `name`（股票名称），用于批量名称查询。

#### 12. 个股所属板块 (`get_belong_boards`)

返回 `List[Dict[str, Any]]`，用于补充个股问股、Agent `get_stock_info` 和主分析流程里的 `belong_boards`：

| 字段 | 类型 | 说明 |
| --- | :---: | --- |
| `name` | str | 板块名称 |
| `code` | str | 板块代码（如 `BK0438`） |
| `type` | str | 板块类型（行业 / 概念 / 地域等，视上游返回而定） |

**当前入口与来源**：

- 统一入口：`DataFetcherManager.get_belong_boards`
- 当前已验证主源：`EfinanceFetcher.get_belong_board` → `ef.stock.get_belong_board`
- 主要调用方：
  - 个股问股 / Agent 工具：`src/agent/tools/data_tools.py` 的 `get_stock_info`
  - 主分析流程：`src/core/pipeline.py` 将 `belong_boards` 挂到 `fundamental_context`

> 该能力是“**个股 → 所属板块**”，不是“**板块/题材 → 成分股**”。

#### 13. 板块/题材成分股（热点详情 / 轮动候选）

当前仓库里“**板块/题材 → 成分股**”能力不走 `DataFetcherManager`，而是由 AlphaSift 热点详情链路提供，主要用于热点题材详情、后续轮动选股和阶段 2/3 的成分股扩展：

**统一入口**：

- API：`GET /api/v1/alphasift/hotspots/{topic}`
- 服务：`AlphaSiftService.hotspot_detail`
- Provider：`DsaEastMoneyHotspotProvider.hotspot_detail`

**返回字段（`stocks` 列表）**：

| 字段 | 类型 | 说明 |
| --- | :---: | --- |
| `code` | str | 股票代码 |
| `name` | str | 股票名称 |
| `change_pct` | float | 涨跌幅(%)，可为空 |
| `amount` | float | 成交额，可为空 |
| `turnover_rate` | float | 换手率(%)，可为空 |
| `volume_ratio` | float | 量比，可为空 |
| `role` | str | 角色说明，默认 `概念股` / 兜底时可能为 `活跃股` |
| `hot_stock_score` | float | 热门度分数 |

**当前源链路**：

| 场景 | 主源 | 回退 / 补强 | 代码入口 |
| --- | --- | --- | --- |
| 概念成分股 | `ak.stock_board_concept_cons_em(symbol=topic)` | 同花顺概念页 HTML 解析、板块异动龙头兜底、同热点组活跃股补强 | `DsaEastMoneyHotspotProvider.stock_board_concept_cons_em` |
| 行业成分股 | `ak.stock_board_industry_cons_em(symbol=topic)` | 板块异动龙头兜底 | `DsaEastMoneyHotspotProvider.stock_board_industry_cons_em` |
| 行情补强 | DSA `DataFetcherManager.prefetch_realtime_quotes` + `get_realtime_quote` | 为空则保留原始成分股结果 | `DsaEastMoneyHotspotProvider._enrich_constituent_quotes` |
| 缓存 | provider 进程内缓存 + 题材详情磁盘缓存 | 详情磁盘缓存默认 30 分钟 | `data/alphasift/hotspot_details` |

**验证记录（2026-07-03）**：

- 实网验证：`DataFetcherManager.get_belong_boards("600519")` 返回 29 个所属板块，样例包含 `食品饮料`、`白酒Ⅲ`、`贵州板块`
- 实网验证：`DsaEastMoneyHotspotProvider.stock_board_concept_cons_em("玻璃基板")` 返回 11 只概念股
- 实网验证：`DsaEastMoneyHotspotProvider.hotspot_detail("玻璃基板")` 返回 `stock_count=11`，并带有 `change_pct` / `amount` / `turnover_rate` / `volume_ratio` 补强字段
- 单测验证：`tests/test_alphasift_api.py -k "concept_stocks or uses_industry_constituents_for_industry_hotspots"` 通过

**环境注意事项**：

- 原始东财 / AkShare 成分股接口对代理环境较敏感；若出现 `ProxyError` / `RemoteDisconnected`，优先禁用 `HTTP_PROXY` / `HTTPS_PROXY`
- 概念股链路比行业链路多一层同花顺页面兜底，因此在上游波动时通常更稳

#### 14. 板块比价 (`PricingService.price_board`)

`PricingService.price_board(board_id, trade_date)` 会为**单个板块的全部成分股**生成 `pricing_snapshot` / `pricing_factor_run`，供 SPI `sector_rotation` 和 AlphaSift `PricingFilter` 使用。

**调用链入口**：

- 服务入口：`src/services/pricing_service.py` 的 `PricingService.price_board`
- 日终/补齐入口：
  - `SpiTaskRunner.refresh_daily`
  - `SpiDataHydrator._refresh_pricing_stage`

**依赖的数据来源分层**：

| 层级 | 读取内容 | 代码入口 | 外部源 / 存储 | 说明 |
| --- | --- | --- | --- | --- |
| 1 | 板块成分股列表 | `ConstituentSnapshotRepo.get_snapshot_state` / `ConstituentRuntimeResolver.resolve` | 本地 `constituent_snapshot`；缺失时回源 `legulegu` | 当前交易日优先读库；无快照时可沿用最近 22 个交易日内快照；再缺失才请求 `https://legulegu.com/stockdata/index-composition?industryCode={board_id}.SI` |
| 2 | 个股历史日线 | `StockRepository.get_range`（经 `_fetch_bars`） | 本地 `stock_daily` | `price_board` **不直接联网抓 K 线**，只读库中已落地日线；若库里没数据，CMF/RS 会退化 |
| 3 | 实时总市值 | `DataFetcherManager.get_realtime_quote` | 行情数据源优先级链 | 用于读取 `total_mv`；A 股默认链见“实时行情优先级” |
| 4 | 基本面利润字段 | `DataFetcherManager.get_profit_snapshot` | 轻量基本面读取链 | 仅读取 `financial_report.net_profit_parent` / `report_date`，避免逐股走完整 `get_fundamental_context` 聚合 |
| 5 | 资金流代理 | `DataFetcherManager.get_stock_capital_flow_context` | 个股级资金流读取链 | 仅读取 `stock_flow.main_net_inflow` 等个股字段；不再为每只股票重复拉取板块级 `sector_rankings` |

**因子与数据映射**：

| 输出字段 | 依赖输入 |
| --- | --- |
| `rs_score` | 本地日线收益序列 |
| `cmf` | 本地日线 OHLCV |
| `sp_ratio` / `sp_score` | `total_mv` + `net_profit_parent` |
| `flow_score` | `stock_capital_flow_context.stock_flow` |
| `total` | `sp_score + cmf + flow_score` 的板块内加权结果 |

**执行特征**：

- 当前实现是**单板块内串行**处理全部成分股，不做股票级并发
- 每只股票处理完成后固定 `sleep(0.5s)`，主要为了压低资金流/行情链路的节奏
- AlphaSift `sector_rotation` 实时选股在 BUY 扫描前会先检查本地 `stock_daily`；若成分股缺少足够日线，会通过 `DataFetcherManager.get_daily_data` 并行补齐后再继续扫描/比价，并发度由 `strategies/rotation_entry.yaml` 的 `daily_history.max_workers` 控制
- 因此外部源压力主要来自：
  - `legulegu` 成分股页面
  - 缺失日线时的 `get_daily_data`
  - `get_realtime_quote`
  - `get_profit_snapshot`
  - `get_stock_capital_flow_context`

**排障重点**：

- 如果 `pricing_snapshot` 为空，先看 `constituent_snapshot` 是否已有成分股
- 若成分股已存在但 `priced_count=0`，再看 `stock_daily` 是否缺日线、`total_mv` / `net_profit_parent` 是否缺失
- 若运行极慢，优先从 `pricing_top_n`、陈旧快照复用、而不是并发度入手

#### 15. 资金流向 (`get_fund_flow`)

返回个股资金流向数据（主力/散户/超大单/大单/中单/小单净流入）。

### A 股行情

| 数据源 | Fetcher | 底层库/API | 数据类型 | 是否需要 Token | 说明 |
| --- | --- | --- | --- | :---: | --- |
| 腾讯财经 | `TencentFetcher` | 腾讯 HTTP 接口 | 实时行情、日K | 否 | 有量比/换手率/市盈率，单股查询稳定 |
| 新浪财经 | `AkshareFetcher` (sina) | akshare | 实时行情 | 否 | 基本行情稳定，但无量比 |
| 东方财富 | `EfinanceFetcher` | efinance | 实时行情、日K | 否 | 数据最全但容易被封，流控严格 |
| 东方财富 | `AkshareFetcher` (em) | akshare (东方财富源) | 实时行情、日K | 否 | 同上，全量接口 |
| Tushare | `TushareFetcher` | tushare | 实时行情、日K、财务、基本面 | 需要 (2000积分起) | 数据最全面，付费用户优先 |
| 通达信 | `PytdxFetcher` | pytdx | 历史日K | 否 | 通达信协议，稳定但数据有限 |
| Baostock | `BaostockFetcher` | baostock | 历史日K | 否 | 免费 A 股历史数据，更新滞后 |

实时行情优先级（默认）：`tencent → akshare_sina → efinance → akshare_em`

### 港股/美股行情

| 数据源 | Fetcher | 底层库/API | 数据类型 | 是否需要 Token | 说明 |
| --- | --- | --- | --- | :---: | --- |
| YFinance | `YfinanceFetcher` | yfinance | 实时行情、日K、基本面 | 否 | 美股/港股主要数据源，免费 |
| 长桥 | `LongbridgeFetcher` | Longbridge OpenAPI | 实时行情、日K、静态信息 | OAuth/Legacy Key | 美股/港股高质量数据，兜底用 |
| 东方财富 | `EfinanceFetcher` | efinance | 港股实时行情 | 否 | 港股可作为补充 |
| Finnhub | `FinnhubFetcher` | Finnhub API | 美股基本面、新闻 | 需要 (免费额度) | 美股基本面补充 |
| Alpha Vantage | `AlphaVantageFetcher` | Alpha Vantage API | 美股/全球基本面 | 需要 (免费额度) | 全球市场基本面 |

### 行情源优先级策略

**有 Tushare Token 时**：
1. Tushare (P0) / Efinance (P0)
2. Akshare (P1)
3. Pytdx (P2)
4. Baostock (P3)
5. YFinance (P4)
6. Longbridge (P5，美股/港股兜底)

**无 Tushare Token 时**：
1. Efinance (P0)
2. Akshare (P1)
3. Pytdx (P2) / Tushare (P2，不可用)
4. Baostock (P3)
5. YFinance (P4)
6. Longbridge (P5)

### 配置项

```env
TUSHARE_TOKEN=                            # Tushare Pro Token
LONGBRIDGE_OAUTH_CLIENT_ID=               # 长桥 OAuth client_id
LONGBRIDGE_APP_KEY=                       # 长桥 Legacy App Key
LONGBRIDGE_APP_SECRET=                    # 长桥 App Secret
LONGBRIDGE_ACCESS_TOKEN=                  # 长桥 Legacy Access Token
LONGBRIDGE_HTTP_URL=                      # 长桥 HTTP 接口 (默认 https://openapi.longbridge.com)
REALTIME_SOURCE_PRIORITY=tencent,akshare_sina,efinance,akshare_em
PREFETCH_REALTIME_QUOTES=true             # 预取实时行情
ENABLE_REALTIME_QUOTE=true                # 实时行情开关
ENABLE_CHIP_DISTRIBUTION=false            # 筹码分布 (不稳定)
ENABLE_EASTMONEY_PATCH=false              # 东财接口补丁
```

---

## 二、搜索与新闻数据源

搜索提供者通过 `src/search_service.py` 的 `SearchService` 统一管理，按优先级链式 fallback。

| 数据源 | Provider | API 端点 | 特点 | 是否需要 Key | 默认配额 |
| --- | --- | --- | --- | :---: | --- |
| Grok | `GrokSearchProvider` | `https://api.x.ai/v1/chat/completions` | LLM 驱动广度+深度搜索，JSON 结构化返回 | 需要 | 付费 |
| Anspire | `AnspireSearchProvider` | Anspire AI Search API | 中文内容优化，实时搜索 | 需要 | 付费 |
| Bocha | `BochaSearchProvider` | 博查搜索 API | 中文搜索优化，AI 摘要 | 需要 | 付费 |
| Tavily | `TavilySearchProvider` | `https://api.tavily.com/search` | AI 优化搜索，免费额度 | 需要 | 1000次/月 |
| Brave | `BraveSearchProvider` | `https://api.search.brave.com` | 隐私优先，美股优化 | 需要 | 2000次/月 |
| SerpAPI | `SerpAPISearchProvider` | `https://serpapi.com/search` | 多引擎聚合 | 需要 | 100次/月 |
| MiniMax | `MiniMaxSearchProvider` | MiniMax Coding Plan API | 结构化搜索结果 | 需要 | 付费 |
| SearXNG | `SearXNGSearchProvider` | 自建/公共实例 | 匿名无配额兜底 | 否 | 无限 |

### 配置项

```env
GROK_API_KEYS=                            # Grok API Key (逗号分隔)
GROK_BASE_URL=https://api.x.ai/v1         # Grok API base URL
GROK_MODEL=grok-4.20-fast                 # Grok 模型
ANSPIRE_API_KEYS=                         # Anspire Search API Key
BOCHA_API_KEYS=                           # 博查搜索 API Key
TAVILY_API_KEYS=                          # Tavily API Key
BRAVE_API_KEYS=                           # Brave Search API Key
SERPAPI_API_KEYS=                         # SerpAPI Key
MINIMAX_API_KEYS=                         # MiniMax API Key
SEARXNG_BASE_URLS=                        # SearXNG 自建实例
SEARXNG_PUBLIC_INSTANCES_ENABLED=true     # 自动发现公共实例
NEWS_MAX_AGE_DAYS=3                       # 新闻最大时效
NEWS_STRATEGY_PROFILE=short               # 时效策略档位
```

---

## 三、本地资讯情报源 (RSS/Atom/NewsNow)

通过 `IntelligenceService` 管理合规资讯源，定时拉取并落库。

| 类型 | 说明 | 配置项 |
| --- | --- | --- |
| RSS/Atom | 用户自定义 RSS 源 | `POST /api/v1/intelligence/sources` |
| NewsNow | 聚合热点平台 JSON API | `NEWSNOW_BASE_URL` (默认 `https://newsnow.busiyi.world`) |

内置 NewsNow 默认源：

| 源 ID | 名称 | 侧重 |
| --- | --- | --- |
| `cls-hot` | 财联社热门 | A 股题材热点 |
| `xueqiu-hotstock` | 雪球热门股票 | 个股关注度 |
| `wallstreetcn-quick` | 华尔街见闻快讯 | 宏观/商品/市场事件 |
| `jin10` | 金十数据 | 全球宏观/外盘 |
| `gelonghui` | 格隆汇事件 | 港股/中概股 |

安全边界：URL 仅允许 `http/https` 绝对地址，禁止 `localhost`/内网/回环地址，拉取时禁用环境代理，重定向后二次校验。

---

## 四、LLM / AI 模型

通过 LiteLLM 统一路由，支持多 Provider 多模型。

| Provider | 配置 Key | 说明 |
| --- | --- | --- |
| Gemini | `GEMINI_API_KEYS` | 主模型，Google AI |
| Anthropic Claude | `ANTHROPIC_API_KEYS` | 备选模型 |
| OpenAI | `OPENAI_API_KEYS` | 兼容 API，支持自定义 Base URL |
| DeepSeek | `DEEPSEEK_API_KEYS` | 国产模型 |
| 自定义渠道 | `LLM_CHANNELS` | 多渠道声明式配置 |
| LiteLLM YAML | `LITELLM_CONFIG` | 完整 LiteLLM 配置文件路径 |

### 配置项

```env
LITELLM_MODEL=                            # 主模型 (provider/model 格式)
LITELLM_FALLBACK_MODELS=                  # 跨模型 fallback 列表
GEMINI_API_KEYS=                          # Gemini API Key
ANTHROPIC_API_KEYS=                       # Anthropic API Key
OPENAI_API_KEYS=                          # OpenAI API Key
OPENAI_BASE_URL=                          # OpenAI 兼容 Base URL
DEEPSEEK_API_KEYS=                        # DeepSeek API Key
LLM_CHANNELS=                             # 多渠道声明
LITELLM_CONFIG=                           # LiteLLM YAML 配置文件路径
LLM_TEMPERATURE=0.7                       # 全局温度参数
LLM_TIMEOUT_SEC=                          # 单次请求超时
LLM_MAX_TOKENS=                           # 输出 token 上限
```

---

## 五、社交舆情数据源

| 数据源 | 服务 | API | 适用市场 | 是否需要 Key |
| --- | --- | --- | :---: | :---: |
| Social Sentiment | `SocialSentimentService` | `https://api.adanos.org` | 美股 | 需要 |

```env
SOCIAL_SENTIMENT_API_KEY=                 # api.adanos.org API Key
SOCIAL_SENTIMENT_API_URL=                 # 默认 https://api.adanos.org
```

---

## 六、通知推送渠道

| 渠道 | Sender | 协议 | 配置项 |
| --- | --- | --- | --- |
| 企业微信 | `wechat_sender` | Webhook | `WECHAT_WEBHOOK_URL` |
| 企业微信 (Bot) | `wecom` | 回调 | `WECOM_CORPID`/`WECOM_TOKEN`/`WECOM_ENCODING_AES_KEY`/`WECOM_AGENT_ID` |
| 飞书 | `feishu_sender` | Webhook | `FEISHU_WEBHOOK_URL` |
| 飞书 (App Bot) | `feishu_sender` | API | `FEISHU_CHAT_ID`/`FEISHU_DOMAIN` |
| 钉钉 | `custom_webhook_sender` | Webhook | `CUSTOM_WEBHOOK_URLS` |
| Telegram | `telegram_sender` | Bot API | `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` |
| Discord | `discord_sender` | Webhook/Bot | `DISCORD_WEBHOOK_URL`/`DISCORD_BOT_TOKEN` |
| Slack | `slack_sender` | Webhook/Bot | `SLACK_WEBHOOK_URL`/`SLACK_BOT_TOKEN` |
| 邮件 | `email_sender` | SMTP | `EMAIL_SENDER`/`EMAIL_PASSWORD`/`EMAIL_RECEIVERS` |
| Pushover | `pushover_sender` | API | `PUSHOVER_USER_KEY`/`PUSHOVER_API_TOKEN` |
| PushPlus | `pushplus_sender` | API | `PUSHPLUS_TOKEN` |
| Server酱3 | `serverchan3_sender` | API | `SERVERCHAN3_SENDKEY` |
| ntfy | `ntfy_sender` | HTTP | `NTFY_URL` |
| Gotify | `gotify_sender` | HTTP | `GOTIFY_URL`/`GOTIFY_TOKEN` |
| AstrBot | `astrbot_sender` | HTTP | `ASTRBOT_TOKEN`/`ASTRBOT_URL` |
| 自定义 Webhook | `custom_webhook_sender` | HTTP POST JSON | `CUSTOM_WEBHOOK_URLS`/`CUSTOM_WEBHOOK_BEARER_TOKEN` |

---

## 七、AlphaSift 选股引擎

| 数据源 | 说明 | 配置项 |
| --- | --- | --- |
| AlphaSift | 独立外部选股引擎，通过 `alphasift.dsa_adapter` 适配层接入 | `ALPHASIFT_ENABLED` |

AlphaSift 内部使用：Tencent 日K、Sina 快照、东方财富、AkShare 等数据源，由 AlphaSift 自身管理，不在 DSA 侧配置。

---

## 八、数据源依赖关系图

```
                       ┌──────────────────────────────┐
                       │        Config (.env)          │
                       └──────────┬───────────────────┘
                                  │
          ┌───────────────────────┼───────────────────────┐
          │                       │                       │
          ▼                       ▼                       ▼
   ┌──────────────┐      ┌──────────────┐       ┌──────────────┐
   │ 行情 Data     │      │ 搜索 Search   │       │ LLM AI       │
   │ Provider      │      │ Provider      │       │ Provider      │
   └──────┬───────┘      └──────┬───────┘       └──────┬───────┘
          │                     │                      │
   Tushare → Efinance    Grok → Anspire         Gemini → Claude
   → Akshare → Pytdx     → Bocha → Tavily      → OpenAI → DeepSeek
   → Baostock → YFinance → Brave → SerpAPI     → Custom Channels
   → Longbridge           → MiniMax → SearXNG
   → Tencent              (链式 fallback)       (LiteLLM 路由)
   → Finnhub/AlphaVantage
   (优先级 fallback)

   ┌──────────────┐      ┌──────────────┐       ┌──────────────┐
   │ 资讯 Intelligence│   │ 通知 Notification│    │ 社交舆情      │
   └──────┬───────┘      └──────┬───────┘       └──────┬───────┘
          │                     │                      │
   RSS/Atom 源           企业微信/飞书/TG         api.adanos.org
   NewsNow JSON API       Discord/Slack/邮件        (美股 only)
   (定时拉取落库)          Pushover/ntfy/Gotify
                          自定义 Webhook
                          (全渠道并行推送)
```

---

## 九、流控与容错

| 机制 | 配置 | 说明 |
| --- | --- | --- |
| Akshare 请求间隔 | `AKSHARE_SLEEP_MIN`/`AKSHARE_SLEEP_MAX` (默认 2-5s) | 随机间隔防封禁 |
| Tushare 限流 | `TUSHARE_RATE_LIMIT_PER_MINUTE` (默认 80) | 每分钟最大请求数 |
| 搜索重试 | 3 次指数退避 (1-10s) | Transient 网络错误自动重试 |
| 搜索 Key 隔离 | 单 Key 错误 ≥3 次自动跳过 | 多 Key 负载均衡 |
| 熔断器 | `CIRCUIT_BREAKER_COOLDOWN` (默认 300s) | 数据源熔断后冷却时间 |
| 缓存 TTL | 搜索 600s / 行情 600s / 基本面 120s | 减少重复请求 |
| 长桥冷却 | `LONGBRIDGE_CONNECTION_COOLDOWN_SECONDS` (默认 15s) | 连接异常后跳过 |
