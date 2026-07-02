# 架构总览

## 顶层结构

| 目录 | 职责 |
| --- | --- |
| `main.py` | 分析任务主入口，支持定时/单次/服务模式 |
| `server.py` | FastAPI 服务启动入口 |
| `src/core/` | 主流程编排：pipeline、market_review、agent runner |
| `src/services/` | 业务服务层：搜索、分析、AlphaSift、舆情 |
| `src/repositories/` | 数据访问层：数据库读写封装 |
| `src/schemas/` | Pydantic 数据模型与 Schema |
| `src/agent/` | AI Agent 工具链：搜索工具、分析工具 |
| `src/llm/` | LLM 调用封装、LiteLLM 路由 |
| `src/notification_sender/` | 多渠道推送：企微/飞书/Telegram/Discord/Slack/邮件 |
| `data_provider/` | 多数据源适配与 fallback：Tushare/Akshare/Efinance/Baostock/Yfinance 等 |
| `api/` | FastAPI REST API |
| `apps/dsa-web/` | React 前端 |
| `apps/dsa-desktop/` | Electron 桌面端 |
| `bot/` | 机器人接入层 |

## 核心数据流

```
配置(.env) → Config → SearchService(+Groks) + DataProvider(fallback chain)
                         ↓                              ↓
                   新闻搜索结果              行情/基本面/财务数据
                         ↓                              ↓
                    AnalysisContextPack 组装
                         ↓
                 LLM 分析 (LiteLLM 路由)
                         ↓
                报告生成 + 通知推送 + Web 展示
```

## 搜索子系统架构

`SearchService` 管理多个 `BaseSearchProvider` 实现，按优先级链式 fallback：

| 优先级 | Provider | 特点 |
| --- | --- | --- |
| 1 | AnspireSearchProvider | 中文搜索优化 |
| 2 | GrokSearchProvider | LLM 驱动的实时联网搜索，JSON 结构化返回 |
| 3 | BochaSearchProvider | 中文搜索优化 + AI 摘要 |
| 4 | TavilySearchProvider | 免费额度，AI 优化 |
| 5 | BraveSearchProvider | 隐私优先，美股优化 |
| 6 | SerpAPISearchProvider | 多引擎聚合 |
| 7 | MiniMaxSearchProvider | Coding Plan Web Search |
| 8 | SearXNGSearchProvider | 自建/公共实例兜底 |

搜索模式：

- **个股新闻搜索** (`search_stock_news`)：顺序 fallback，优先返回含个股直接命中的中文新闻
- **多维度情报搜索** (`search_comprehensive_intel`)：维度 → provider 固定轮询分配，维度间并行执行

Provider 通用能力：

- 多 Key 轮询负载均衡
- 错误计数与自动隔离（单 Key 错误 ≥3 次临时跳过）
- Transient 网络错误自动重试 (3 次，指数退避)

## 配置层

`Config` (src/config.py) 从 `.env` 统一解析所有配置项，通过 `env_prefix` 约定的嵌套模型 (LLM 渠道、Provider Keys、通知渠道) 注入各模块。

## 依赖边界

- `src/services/` 可依赖 `src/repositories/`、`src/schemas/`、`data_provider/`
- `src/core/` 编排 `src/services/`，不直接访问数据源
- `api/` 依赖 `src/services/`，不绕过服务层直调 repository
- `data_provider/` 零依赖（不反向依赖 `src/`）
- AlphaSift 通过 `alphasift.dsa_adapter` 稳定适配层注入，不把 AlphaSift 策略逻辑复制进主仓库
