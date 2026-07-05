# 申万一级板块成分股批量入库

用于按交易日批量抓取 **申万一级板块** 的成分股，并落盘到 `constituent_snapshot` 表，供板块轮动 / 板块比价链路复用。

## 适用场景

- 需要提前补齐 `constituent_snapshot`，减少日终轮动任务因成分股缺失而跳过
- 需要在前端联调前，先把申万一级板块成分股批量落库
- 需要单独重跑部分板块的成分股快照

## 使用方式

默认按 `spi_time()` 对应交易日执行，并在**相邻板块请求之间至少等待 30 秒**：

```bash
.venv/bin/python scripts/sync_sw_constituents.py
```

指定交易日：

```bash
.venv/bin/python scripts/sync_sw_constituents.py --date 2026-07-03
```

只同步部分板块：

```bash
.venv/bin/python scripts/sync_sw_constituents.py --date 2026-07-03 --board-ids 801010,801030,801080
```

强制覆盖当日已有快照：

```bash
.venv/bin/python scripts/sync_sw_constituents.py --date 2026-07-03 --force
```

## 参数说明

- `--date`：写入快照的交易日；默认取 `spi_time()`
- `--board-ids`：逗号分隔的申万一级板块代码列表
- `--interval-seconds`：板块间最小请求间隔，默认 `30`
- `--force`：即使当日快照已存在也重新抓取并覆盖

## 输出说明

脚本输出 JSON 摘要，包含：

- `trade_date`：目标交易日
- `board_count`：本次处理的板块数
- `saved`：成功抓取并写入的板块数
- `skipped_existing`：因当日快照已存在而跳过的板块数
- `failed_fetch`：抓取失败的板块数
- `boards[]`：逐板块结果（`saved / skipped_existing / failed_fetch`）

## 板块列表来源

脚本优先通过 `AkshareSwAdapter.get_sw_first_levels()` 获取申万一级板块列表。

若上游 `sw_index_first_info` 临时不可用，则会回退到数据库内最近一次 `plate_spi_snapshot` 的板块全集合作为板块 universe，以保证成分股补齐脚本仍可执行。

## 速率限制说明

- `ConstituentFetcher.fetch()` 单次请求后仍保留原有 `0.5s` 短暂停顿
- 本脚本额外保证**相邻板块请求起点之间至少间隔 30 秒**
- 对同一板块重复抓取时，`ConstituentFetcher` 仍保留已有的单板块 `30s` 重拉保护
