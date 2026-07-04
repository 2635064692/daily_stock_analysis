# SPI 数据补齐

用于在本地或测试环境同步补齐 SPI 链路数据，减少 AlphaSift `sector_rotation` 联调时因 `v2_score`、轮动信号或 `pricing_snapshot` 缺失导致的空跑。

## 适用场景

- 已有 `plate_spi_snapshot` 基础表，但缺少 `v2_score`
- 需要一次性生成当日轮动 BUY/SELL 信号
- 需要为 Top N 板块补齐 `pricing_snapshot`
- 需要快速判断当前交易日是否满足 `sector_rotation` 候选联调条件

## 使用方式

默认按 `spi_time()` 对应的有效交易日补齐：

```bash
.venv/bin/python scripts/hydrate_spi_data.py
```

指定交易日并限制比价板块数量：

```bash
.venv/bin/python scripts/hydrate_spi_data.py --date 2026-07-03 --pricing-top-n 20
```

## 输出说明

脚本会输出 JSON，包含：

- `stages.v1`：SPI v1 快照刷新结果
- `stages.v2`：SPI v2 评分刷新结果
- `stages.rotation`：轮动信号生成结果
- `stages.pricing`：Top N 板块比价补齐结果
- `readiness`：联调 readiness 摘要

`stages.pricing` 内部依赖的成分股、日线、实时市值、基本面利润与资金流来源，见 `docs/external-data-sources.md` 中“板块比价 (`PricingService.price_board`)”小节。

## 成分股快照复用

为降低成分股接口调用频率，当前实现对**当前有效交易日**增加了快照沿用策略：

- 优先读取 `constituent_snapshot` 当日快照
- 若当日缺失，则允许沿用最近 **22 个交易日** 内的最近一次成功快照
- 沿用写回当日快照时，会记录：
  - `origin_trade_date`
  - `is_stale`
  - `snapshot_age_days`
- 当外部源不可用时，`rotation/pricing` 可先基于陈旧快照继续运行

为避免短时间内反复打外部源，成分股实时重拉默认增加 **30 秒** 间隔。

其中 `readiness` 的关键字段为：

- `sector_rotation_smoke_ready`：只要求存在 `v2_score`
- `sector_rotation_candidate_ready`：同时要求存在 `v2_score`、BUY 信号和 `pricing_snapshot`

## 历史日期限制

默认**不对历史日期**生成轮动信号和板块比价，因为这两步依赖 point-in-time 成分股语义。

- 历史日期默认仅补齐 `v1/v2`
- `rotation/pricing` 会返回 `skipped`
- 若确实只想为排障目的强行跑历史派生数据，可显式传：

```bash
.venv/bin/python scripts/hydrate_spi_data.py --date 2026-07-02 --allow-historical-derived-data
```

该模式仅建议用于本地诊断，不应作为正式历史回补依据。
