## [2026-07-03 20:30] | Task: 归档 SPI phase2 review 结论与修复闭环

### 🤖 Execution Context

- **Agent ID**: codex-cli
- **Base Model**: GPT-5.2
- **Runtime**: 本机（代码修改 + 本地验证）

### 📥 User Query

> 将修改 review 的相关内容 归档到docs中

### 🛠 Changes Overview

**Scope:** `SPI phase2` review 结论沉淀、修复闭环说明、与产品规格文档的关联归档。

**Key Actions:**

- **[归档 review 结论]**: 将 `.claude/reviews/spi-rotation-phase2-review.md` 的 P0/P1 发现项，按 spec/task 条款重新整理为可长期保留的工程记录。
- **[记录修复闭环]**: 说明本轮对成分股快照、SELL 可达性、SPI v2 边界条件和 YAML 配置接入的实际修复方案。
- **[沉淀验证证据]**: 记录本地 py_compile / flake8 / pytest / API smoke 的验证结果，避免后续重复追溯。

### 🧠 Design Intent (Why)

`.claude/reviews/` 更偏向一次性审查产物，不适合作为长期工程知识入口。本次将 review 结论和已完成的修复闭环沉淀到 `docs/histories/`，便于后续在 SPI spec、任务回顾和回归排查时直接引用。

### 📁 Files Modified

- `docs/histories/2026-07-03-spi-rotation-phase2-review-closure.md`（新建，本文件）
- `docs/product-specs/spi-rotation-pricing-requirement-boundary.md`（增加关联入口）

---

## 一、背景

SPI phase2 对应的权威需求边界在：

- `docs/product-specs/spi-rotation-pricing-requirement-boundary.md`
- `.ccg/tasks/spi-rotation-phase2/tasks.md`

在第一轮实现完成后，代码 review 发现虽然表结构、因子模块、API 和单测框架都已落地，但「轮动闭环」与「策略配置生效」两条主语义仍存在阻断缺口，因此形成了 `changes requested` 结论。

---

## 二、review 原始发现项

### P0-1 成分股快照没有真正积累

**对应条款**

- Spec `R11`：按交易日锁定 point-in-time 成分股快照
- WBS `2.4` / `2.8`
- Task `4.1` / `5.2`

**原问题**

- `refresh_daily()` 只读取 `ConstituentSnapshotRepo.get_constituents()`
- 生产链路没有调用 `ConstituentFetcher.fetch()`
- 也没有调用 `save_constituents()`

这意味着如果没有外部预灌数据，`constituent_snapshot` 表不会随着日终任务自动积累，后续轮动信号会长期因为“查无快照”而被跳过。

### P0-2 `board_rank_out_of_top_m` 在调度流里不可达

**对应条款**

- Spec `R10`
- 板块轮动闭环数据流 Step4
- WBS `2.5`

**原问题**

- 调度流只遍历“当天 Top N 观察池”
- 同一轮里又用 `check_exit(top_m=50)` 做退出判断
- 被遍历的板块天然仍在 Top 50 内，因此“板块跌出 Top M”分支在正常日终流里不可达
- 同时观察池状态仅保存在内存中，不具备跨日持续性

### P1-1 `ema_for_spi_v2` exact-period 边界崩溃

**对应条款**

- Task `3.1`：`ema_for_spi_v2(close_series) -> dict[int, list]`
- Task `2.2` / `2.3`：所有因子与打分链路在合法输入上不应崩溃

**原问题**

- 当 `len(close_series) == period` 时，旧实现返回空列表
- `SeparationFactor` 后续直接取 `[-1]`
- 本地最小复现 `cal_stock_spi_v2([1,2,3,4,5], periods=[5])` 抛出 `IndexError`

### P1-2 `rotation_entry.yaml` 未真正接入运行时

**对应条款**

- WBS `2.6`
- Task `4.3`

**原问题**

- YAML 文件虽然存在，但运行时没有加载链路
- `entry_ema_period`、`volume_ratio_threshold`、`exit_top_m`、`exit_ema_period` 和 `pullback_tolerance` 仍是 service 里的硬编码默认值

---

## 三、本轮修复闭环

### 1. 成分股快照改为日终任务内真实抓取并落盘

当前 `RotationService.generate_signals(trade_date)` 的流程为：

1. 按 `v2_score` 取当日观察池
2. 对每个观察池板块先查询快照
3. 若当日快照不存在，则调用 `ConstituentFetcher.fetch(board_id)`
4. 抓取成功后立刻 `save_constituents(board_id, trade_date, stock_codes)`
5. 之后才执行入场信号判断

这样满足了 Spec 中“从启用日起按交易日积累真实成分股名单”的要求。

### 2. SELL 改为基于历史 BUY 信号推导 active positions

为了让“跌出 Top M”退出语义在当前数据库模型下可达，本轮没有新增持仓表，而是引入了一个最小闭环：

- 从 `SpiRotationSignal` 中查询 `before_date` 之前每个 `(board_id, stock_code)` 的最新动作
- 最新动作为 `BUY` 的，视为当前 active position
- 日终退出检查针对这些 active positions 执行
  - 若板块跌出 `Top M` → 直接发出 `SELL`
  - 若仍在 `Top M` 内 → 再按 `exit_ema_period` 做个股 EMA 退出

这让退出逻辑不再依赖“当天仍在观察池”的板块集合，修通了 review 中指出的不可达问题。

> 注：这里是一个**最小可行闭环**，仍不是完整的持仓状态机；如果后续要和真实持仓/回测联动，建议单独引入显式 position/watch state 持久化。

### 3. 修正 `ema_for_spi_v2` 的 exact-period 语义

当前实现将每个周期的首个值定义为该周期的 `SMA seed`，因此：

- `len(close_series) == period` 时返回长度为 `1` 的 EMA 序列
- 返回长度统一为 `len(close_series) - period + 1`

这样既与 docstring 语义一致，也避免了下游因子在边界输入上拿到空列表。

### 4. 将 `rotation_entry.yaml` 真正接入运行时

本轮新增 `RotationStrategyConfig` 与 `load_rotation_strategy_config()`，从 `strategies/rotation_entry.yaml` 读取：

- `watchpool.top_n`
- `entry.ema_period`
- `entry.volume_ratio_threshold`
- `entry.pullback_tolerance`
- `exit.top_m`
- `exit.ema_period`

`RotationService.generate_signals()`、`check_entry()`、`check_exit()` 默认使用这些配置，从而让 task `4.3` / WBS `2.6` 的“YAML 加载、参数生效”具备真实落点。

---

## 四、验证结果

### 1. 静态检查

已通过：

```bash
python3 -m py_compile src/services/spi/spi_calculator.py \
  src/services/spi/spi_scorer.py \
  src/services/spi/rotation_service.py \
  src/services/spi/plate_spi_service.py \
  src/services/spi/spi_task_runner.py \
  src/utils/constituents_snapshot.py \
  src/repositories/plate_spi_repo.py \
  api/v1/endpoints/plate_spi.py \
  src/storage.py \
  tests/test_spi_calculator.py \
  tests/test_spi_factors.py \
  tests/test_rotation_service.py \
  tests/test_spi_task_runner.py
```

```bash
./.venv/bin/flake8 src/services/spi/rotation_service.py \
  src/repositories/plate_spi_repo.py \
  src/services/spi/spi_calculator.py \
  src/services/spi/spi_factors/alignment.py \
  src/services/spi/spi_factors/separation.py \
  tests/test_rotation_service.py \
  tests/test_spi_calculator.py \
  tests/test_spi_factors.py \
  tests/test_spi_task_runner.py \
  --select=E9,F63,F7,F82
```

### 2. 业务单测

已通过：

```bash
./.venv/bin/pytest \
  tests/test_spi_factors.py \
  tests/test_spi_scorer.py \
  tests/test_spi_calculator.py \
  tests/test_constituents_snapshot.py \
  tests/test_rotation_service.py \
  tests/test_spi_task_runner.py -q
```

结果：`95 passed`

### 3. API smoke

已补充并通过：

```bash
./.venv/bin/pytest tests/test_plate_spi_api.py -q
```

覆盖：

- `GET /api/v1/plate-spi/v2/rankings`
- `GET /api/v1/plate-spi/rotation/signals`

结果：`2 passed`

---

## 五、当前结论

本轮 review 中提出的 P0 / P1 阻断项已完成修复，`spi-rotation-phase2` 任务状态已从 review 阶段的 `changes_requested` 收敛到 `passed`。

已解决的问题包括：

- 成分股快照不再只是定义表结构，而是会在日终链路中真实积累
- `board_rank_out_of_top_m` 不再是不可达分支
- `ema_for_spi_v2` 的 exact-period 输入不会再导致崩溃
- `rotation_entry.yaml` 不再是孤立文件，而是进入了运行时配置链路

---

## 六、后续建议

1. 如果 phase2 要继续向“真实轮动状态机”演进，建议新增持久化的 watch / position state，而不是仅依赖历史 BUY/SELL 信号推导。
2. 如果后续需要更强的配置治理，可把 `rotation_entry.yaml` 接入统一配置加载/热更新机制，而不是仅在 service 内部按文件读取。
3. 如果需要对外做阶段验收，可把本文件与 `docs/product-specs/spi-rotation-pricing-requirement-boundary.md` 一并作为 phase2 的实现闭环证据。
