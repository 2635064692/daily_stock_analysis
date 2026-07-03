# phase3 比价系统 P0 — 实施计划 plan.md

> full-collaborate Phase 3 产出。双模型 architect 交叉综合。
> Codex: §30 完整实现方案（目录/签名/表/伪代码/分层/测试/裁剪）。
> Gemini: 互补补充（类结构/边界/Flow归一化/API Schema/日终/陷阱）。

## 双模型裁定

| 议题 | Codex | Gemini | **裁定** |
|---|---|---|---|
| 成分股 fallback | 历史日不 fallback，missing_constituents | get_constituents_with_fallback(含latest) | **Codex**：保护 point-in-time，历史日缺→missing_constituents |
| Flow 归一化 | 板块内百分位 | 百分位（抗极值，优于Z-score） | **一致**：板块内百分位 |
| 复权 | 未细述 | **CMF 必须前复权，除权日污染** | **Gemini 警示**：实现须确认 StockDaily 复权口径 |
| 批次并发 | 未提 | **legulegu 0.5s sleep → 串行** | **Gemini**：price_board 串行，禁并发 |
| 全板块 RS/CMF 缺 | 核心缺→missing_core_factor | 单因子兜底 CMF*1.0 | **Codex**：核心因子缺不伪造总分 |

## 一、目录结构
```
src/services/pricing/__init__.py
src/services/pricing/cmf.py
src/services/pricing/relative_strength.py
src/services/pricing/capital_proxy.py
src/services/pricing_service.py
src/repositories/pricing_repo.py
api/v1/endpoints/plate_pricing.py
src/storage.py (加 PricingSnapshot + PricingFactorRun)
tests/test_pricing_cmf.py
tests/test_pricing_relative_strength.py
tests/test_pricing_capital_proxy.py
tests/test_pricing_service.py
src/services/spi/spi_task_runner.py (refresh_daily 链尾追加)
api/v1/router.py (注册 plate_pricing)
```

## 二、模块函数签名（Codex §30.2，已采纳）
- cmf.calc_cmf(bars, period=20, min_period=5) -> Optional[float]  (原始[-1,1])
- cmf.normalize_cmf(cmf_value) -> Optional[float]  ((cmf+1)/2)
- relative_strength.calc_period_return(closes, period=20) -> Optional[float]
- relative_strength.calc_rs_scores_nullable(stock_returns: list[Optional[float]]) -> list[Optional[float]]  (板块内百分位，缺位保留，n<=1→0.5)
- capital_proxy.extract_flow(stock_flow: dict) -> Optional[float]  (main_net_inflow 优先)
- capital_proxy.normalize_flow_scores(values) -> list[Optional[float]]  (板块内百分位)
- PricingService.price_board(board_id, trade_date) -> dict  (批次级)
- PricingRepository.upsert_pricing / insert_factor_run / find_board_pricing_rank / find_latest_trade_date

## 三、表定义（Codex §30.3）
- PricingFactorRun: board_id, trade_date, constituent_source, rs_window, cmf_window, base_weights_json, effective_weights_json, flow_coverage, constituent_count, priced_count, degraded_count, status, error, created_at（无 UNIQUE，多 rerun 审计）
- PricingSnapshot: board_id, stock_code, trade_date, rs_score, cmf(原始[-1,1]), flow_score, total([0,1]), status, factor_mask, run_id(FK), UNIQUE(board_id,stock_code,trade_date)
- status 枚举: ok / degraded / missing_core_factor / missing_constituents
- **status 双枚举裁定（实施期补充）**：两张表 status 语义分层，不强制统一。
  - PricingSnapshot.status（单股级，plan.md 原四态）：`ok` / `degraded` / `missing_core_factor` / `missing_constituents`
  - PricingFactorRun.status（批次级）：`ok`（全部成分股正常定价）/ `partial`（priced_count>0 但有 degraded/missing_core_factor）/ `failed`（成分股缺失 error="no_constituents"，或 priced_count==0 即全板块缺核心因子 error="all_constituents_missing_core_factor"，error 字段记原因）
  - service 层 price_board 完成后按批次整体结果给 run.status 赋值；单股状态按因子完整度给 snapshot.status 赋值。两套互不覆盖。

## 四、批次级动态重归一化（Codex §30.4 伪代码，核心规则）
1. 收齐 constituents → 每只拉 K 线 + capital_flow_context → raw RS/CMF/Flow
2. 板块内百分位归一化：rs_scores / flow_scores
3. flow_coverage = 有效Flow数/总数；flow_enabled = coverage >= 0.6
4. effective_w = base_w[active] / Σbase_w[active]；active = (rs,cmf,flow) 或 (rs,cmf)
5. 单股：RS或CMF缺 → missing_core_factor, total=None；Flow单只缺但批次启用 → degraded
6. flow_enabled=False 全板块用 RS0.625/CMF0.375
7. 串行执行（legulegu 0.5s sleep，禁并发）

## 五、成分股读路径（Codex §30.5）
- get_constituents(board_id, trade_date) 有 → snapshot
- 无 + 当日 → legulegu fetch + save_constituents → realtime_fetch
- 无 + 历史日 → missing_constituents（不 fallback latest）

## 六、实施分层
- **Layer 1（无依赖，可并行）**: storage.py 表 / cmf.py / relative_strength.py / capital_proxy.py / pricing_repo.py
- **Layer 2（依赖 L1）**: pricing_service.py / plate_pricing.py / spi_task_runner.py 接入 / 4 个 tests

## 七、测试策略（Codex §30.7）
- cmf: 已知K线断言值；high==low→0；Σvol=0→None；不足N→None
- rs: 构造收益序列；并列平均名次；n=1→0.5；缺位保留
- capital_proxy: main_net_inflow优先；fallback inflow_5d；全空None；板块百分位
- service: mock 4组件；flow_coverage阈值切换/单股缺CMF/当日fetch/历史日missing

## 八、日终接入（Codex §30.8 + Gemini E）
refresh_daily 内 rotation.generate_signals 之后追加独立 try/except；首期只算 find_top_boards_v2(top_n=30) 结果，不全量31板块；串行循环。

## 九、裁剪决策（Codex §30.9）
首期不引入：Redis/队列/缓存/批量SQL/单股动态改权重/历史latest fallback/GET内触发计算。

## 十、实现陷阱（Gemini F）
- CMF 复权：须确认 StockDaily 是否前复权；若不复权，除权日 MFM 扭曲污染20天均值 → 实现时核查，缺失则降级
- legulegu 0.5s sleep：price_board 串行，禁 ThreadPool/asyncio
