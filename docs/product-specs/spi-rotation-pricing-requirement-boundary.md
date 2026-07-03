## REQUIREMENT BOUNDARY DOCUMENT
## DSA 板块轮动系统 — SPI 迁移 + 轮动优化 + 比价系统

> 来源：creative-explore 会话，基于 b-quant-chan Java 侧 SPI 实现迁移 + 会话 `e1883821` 规划能力。
> 范围：**纯后端**。三阶段分期交付。
> **2026-07 实现同步说明**：阶段1当前已落地方案为 **`akshare` 申万一级行业指数直算 SPI**（31 个行业指数 close 序列 → `cal_index_spi`），不再以“选股宝成分股聚合”作为阶段1权威方案；成分股 point-in-time 与板块内比价仍保留在阶段2/3。
> **2026-07-03 review / 修复归档**：phase2 首轮 review 的阻断项与修复闭环已归档到 `docs/histories/2026-07-03-spi-rotation-phase2-review-closure.md`。

---

### 1. Problem Statement

DSA（Python，FastAPI）当前只有个股级分析与当日板块涨跌幅排名（`get_sector_rankings`），缺乏"板块强弱的历史可比较度量"与"板块内个股比价"能力。需将 b-quant-chan（Java）已验证的 SPI 板块强弱指标迁移到 DSA，并完整实现其规划中的 SPI v2 增强、板块轮动闭环与比价系统，使 DSA 具备"板块轮动选板块 + 比价选个股 + 信号定买卖点"的后端能力。不涉前端、不涉缠论结构、不涉回测。

---

### 2. In-Scope Requirements（按三阶段）

#### 阶段 1：SPI 原逻辑迁移（SPI v1）

| # | Requirement | Priority | Source |
|---|---|---|---|
| R1 | 新建 **SPI 私有行业指数适配层**：读取 `akshare` 申万一级行业列表与行业指数历史日线（`sw_index_first_info` / `index_hist_sw`），不接入全局 `DataFetcherManager` | P0 | 当前实现 |
| R2 | 复刻 SPI v1 核心计算：8 条斐波那契 EMA(5,13,21,34,55,89,144,233) + 收盘价站上计数(0~8)，**直接以板块/行业指数 close 序列计算 SPI** | P0 | R1/A1 |
| R3 | SPI 快照表：(板块,交易日) upsert 幂等；保留 -1 无效板块（无足够指数历史/锚点无可用数据）哨兵语义及下游过滤 | P0 | S7/S3 |
| R4 | 排名查询：按板块 SPI 降序取 Top N（复刻 `findMostLimit` 语义），返回 N 日 SPI 时序 | P0 | 用户 M2 |
| R5 | 异步回算任务：日终刷新 + 历史补算（fire-and-forget）+ 任务状态机(pending/running/done/failed) + 进度查询接口 | P0 | S6/A3 |
| R6 | SPI 计算并行化（板块级并发），行业指数历史按 `anchor_date` 截止实时拉取；阶段1**不走成分股逐股缓存链路** | P0 | S10/S11 |
| R7 | 集成边界：`get_sector_rankings`/`get_belong_boards`/`capital_flow_context` **只读复用**，不侵入现有调用方 | P0 | S14 |

#### 阶段 2：SPI 板块轮动改造优化（SPI v2 + 轮动闭环）

| # | Requirement | Priority | Source |
|---|---|---|---|
| R8 | SPI v2 因子增强：EMA 方向判断 + 均线多头排列有序度 + 分离度度量 + 压缩/扩张检测；打分扩展到连续空间 | P1 | 会话 e1883821 |
| R9 | v2 打分模型与权重：明确因子组合方式（加权和/乘积/分段）与权重来源，因子以**可插拔模块**注册 | P1 | S1/S17 |
| R10 | 板块轮动闭环：排名预选（Top N 观察池）+ 回踩均线(10/20日)入场信号 + 出场信号（跌破EMA/排名跌出Top M）；先作观察/提示能力 | P1 | S4 |
| R11 | 成分股 point-in-time 锁定：历史快照锁定该交易日真实成分股名单，防前视 | P1 | S2 |

#### 阶段 3：比价系统

| # | Requirement | Priority | Source |
|---|---|---|---|
| R12 | 板块内个股比价：首期口径为板块内个股相对强弱/估值比较，point-in-time 快照 | P1 | 会话 I4 |
| R13 | CMF(Chaikin Money Flow) / 资金流代理因子作为比价增强输入（复用 `capital_flow_context`，非北向资金替代） | P2 | 会话调研3 |
| R14 | 比价 point-in-time 快照保留周期策略 | P2 | S9 |

#### 横切（全阶段）

| # | Requirement | Priority | Source |
|---|---|---|---|
| R15 | 可观测性：SPI 计算失败/板块缺失/数据源降级接入 DSA 现有结构化日志与指标 | P1 | S12 |

---

### 3. Out-of-Scope（明确排除）

| # | Exclusion | Reason |
|---|---|---|
| X1 | 前端展示（TradingView `zh_spi` 或任何图表） | 用户明确"当前仅涉及后端设计" |
| X2 | 北向资金 Z-Score | 用户明确"暂不考虑" |
| X3 | 缠论笔/中枢/线段结构联立确认 | 用户明确"不考虑缠论相关概念" |
| X4 | 回测验证基线（年化/夏普/最大回撤） | 用户明确"不考虑回测" |
| X5 | 日线数据本地缓存 | 用户明确"不缓存K线"（仅并行） |
| X6 | snapshot 表为未来因子做前向兼容预留 | 用户拒绝 S16 |
| X7 | SPI v2 之外的 Level3 深度方案（RRG/DTW 跨板块传导/宏观美林时钟） | 会话列为 L3，超三阶段范围 |
| X8 | 改造现有 `get_sector_rankings`/`get_belong_boards`/`capital_flow_context` 的行为 | 只读复用 |

---

### 4. Deferred Items

无。所有悬而未决项已在分诊中解决。

> 注：北向资金(X2)与缠论联立(X3)为"永久排除本期"而非"延后"，若未来重启需重新走需求探索。

---

### 5. Open Questions

| # | Question | Blocking? | Suggested Resolution Path |
|---|---|---|---|
| Q1 | 选股宝接口的认证/限流/字段口径（板块ID体系、成分股返回结构、历史K线接口）在 DSA 环境是否可达？ | **No（不再阻塞阶段1）** | 阶段1已改为 `akshare` 行业指数直算；若阶段2/3 重启成分股链路，再单独验证选股宝契约 |
| Q2 | SPI v2 因子权重初始值如何确定（无回测约束下）？ | No（阶段2内决） | 用户/经验设定 + 因子可插拔以便后续调整（S17 已保证可调） |
| Q3 | 选股宝与 DSA 现有 akshare/efinance 板块集合是否需要做板块名映射互通？ | No | DSA 内部自洽即可（用户已确认）；若轮动结果需与现有个股分析联动再定 |

---

### 6. Assumptions & Constraints

**Assumptions**
- A1：SPI 用 EMA（非 SMA），日线频率，等价复刻 Java `emaForSPI`（TA-Lib EMA 的 Python 等价实现，如 `pandas-ta`/`talib`/手写 EMA）
- A3：历史补算异步执行，不复刻 Java 同步 HTTP 超时陷阱
- A5：比价 point-in-time 快照为防前视(look-ahead-free)历史快照
- 板块口径 DSA 内部自洽即可，不强求与选股宝/akshare 完全一致

**Constraints**
- 本机仅代码分析/修改 + git push，所有验收在远端（CLAUDE.md §1）
- 严禁影响现有功能（CLAUDE.md 0.2），三个改造复用函数只读
- 代码风格：精简高效、无冗余、非必要不写注释文档
- 数据源 fallback/熔断机制需复用 DSA 现有体系（`data_provider/base.py`）

---

### 7. Requirement Completeness Check

- [x] 核心场景覆盖：SPI 迁移 → v2 增强 → 轮动闭环 → 比价（三阶段）
- [x] 错误/失败流：无效板块 -1 哨兵、数据源降级 fallback、任务 failed 状态
- [x] 数据生命周期：快照 upsert、point-in-time 锁定、保留周期(S9/R14)
- [x] 非功能需求：并行(S10)、可观测性(S12)、不缓存(X5)
- [x] 集成点：行业指数适配层（已实现）、成分股链路（AlphaSift 现有能力，可扩展）、三个现有函数(只读)、任务队列/API异步(复用)
- [x] 已知演进向量：因子可插拔(S17)支撑未来加因子
- [x] 排除项明确：前端/北向/缠论/回测/缓存 全部列入 Out-of-Scope

---

### 8. 三阶段交付边界总览

| 阶段 | 交付 | 关键复用 | 关键重建 | 阻塞项 |
|---|---|---|---|---|
| **1. SPI 原逻辑迁移** | `akshare` 申万一级行业指数适配 + SPI v1 计算 + 快照表 + 排名查询 + 异步回算任务 + 并行 | 任务队列、SQLAlchemy、Repository、API 异步状态 | 行业指数数据源、SPI 快照表、回算任务 | 无 |
| **2. SPI 轮动改造优化** | SPI v2 因子(方向/分离度/排列/压缩) + 可插拔权重模型 + 轮动闭环(观察池/入场/出场) + point-in-time 成分股 | 阶段1全部产出 | v2 因子模块、轮动信号引擎、成分股历史锁定 | 无 |
| **3. 比价系统** | 板块内个股比价 + CMF/资金流代理 + point-in-time 比价快照 | `capital_flow_context`、阶段1板块 SPI/行业指数基础、阶段2 成分股快照 | 比价计算、比价快照表 | 无 |

---
---

# 阶段 1 详细规划：SPI 原逻辑迁移

> 依据：Java 侧 `Indicators.emaForSPI` / `PlateSingleIndicatorsEntity` 核心指标语义 + DSA 现有 `AnalysisTaskQueue` /
> `declarative_base` / `trading_calendar` 范式。
> **当前权威实现**：阶段1使用 `akshare` 申万一级行业指数直算 SPI；早期选股宝成分股迁移方案仅保留为后续阶段参考，不再作为阶段1实施前提。

---

## 9. 修改的结构目录

严格对齐 DSA 现有分层（`data_provider/` 数据源 / `src/repositories/` 持久化 / `src/services/` 业务 /
`api/v1/endpoints/` 接口 / `src/storage.py` 表定义）。**新建为主，零侵入现有文件。**

```
daily_stock_analysis/
├── src/
│   ├── storage.py                  【新增 2 表】PlateSpiSnapshot / SpiBackfillTaskRun（declarative_base 范式）
│   │
│   ├── repositories/
│   │   └── plate_spi_repo.py       【新建】PlateSpiRepository：upsert快照 / Top N 排名 / N日时序 / 任务记录CRUD
│   │
│   ├── services/
│   │   └── spi/
│   │       ├── akshare_sw_adapter.py 【新建】SPI 私有 adapter：申万一级行业列表 + 行业指数历史日线
│   │       ├── spi_calculator.py     【新建】SPI 纯算法：ema_for_spi / cal_stock_spi / cal_index_spi
│   │       ├── spi_time.py           【新建】交易日锚点与 trading-date 迭代
│   │       ├── plate_spi_service.py  【新建】业务编排：单板块 SPI / 全量并行计算
│   │       └── spi_task_runner.py    【新建】异步回算 / 日终刷新 / SPI 任务互斥
│   │
│   └── main.py                     【改 1 处】在 `scheduled_task()` 主分析后追加 SPI 日终刷新
│
├── api/v1/endpoints/
│   └── plate_spi.py                【新建】POST /refresh（触发日终）/ POST /backfill（触发历史回算）/
│                                          GET /status/{task_id}（复用 AnalysisTaskQueue）/ GET /rankings（Top N）
│
├── tests/
│   ├── test_spi_calculator.py      【新建】EMA / index SPI 纯算法单测
│   ├── test_akshare_sw_adapter.py  【新建】申万一级 / 行业指数字段映射单测
│   ├── test_plate_spi_service.py   【新建】并行计算 / anchor_date / upsert 幂等 单测
│   └── test_spi_task_runner.py     【新建】交易日回算 / 任务互斥 / 元数据落库 单测
```

**改动统计（按当前实现）**：以 `src/services/spi/` 新建为主，改动集中在 `storage.py` / `api/v1/router.py` / `api/v1/endpoints/plate_spi.py` / `main.py`。**不触碰** `get_sector_rankings`/`get_belong_boards`/`capital_flow_context` / `data_provider/base.py`。

---

## 10. 数据流向

### 10.1 写流（计算 → 落库）

```
AkShare 申万行业接口
   │
   │  ① sw_index_first_info()   → 申万一级行业列表 [{行业代码, 行业名称, 成份个数}]
   │  ② index_hist_sw(symbol)   → 单行业指数历史日线（开高低收）
   ▼
AkshareSwAdapter (src/services/spi/akshare_sw_adapter.py)
   │  统一字段映射：`801010.SI` → `801010`，`日期` → `date`，`收盘` → `close`
   ▼
PlateSpiService.plate_spi_snapshot(board_id, trade_date)
   │  并行（ThreadPoolExecutor，板块级并发）
   │  对每个行业指数：
   │    get_index_kline(board_id, back_count=300, end_date=anchor_date)
   │       → 截止 anchor_date 的 close 序列
   │    SpiCalculator.ema_for_spi(close_series)
   │       → {5,13,21,34,55,89,144,233: ema_value}（数据不足该周期则跳过）
   │    SpiCalculator.cal_index_spi(close_series)
   │       → 整数 0~8（指数收盘价站上几条 EMA）
   │  coverage = min(len(close_series)/233, 1.0)，不足 233 根标记 low confidence
   ▼
PlateSpiRepository.upsert_snapshot(board_id, trade_date, spi_value, confidence, coverage, board_name)
   │  (board_id, trade_date) 唯一约束 → 幂等
   ▼
PlateSpiSnapshot 表 (storage.py)
```

### 10.2 读流（查询 → 接口）

```
GET /api/v1/plate-spi/rankings?date=&top_n=30&days=100
   ▼
PlateSpiRepository.find_top_boards(date, top_n=30)
   │  WHERE trade_date=anchor AND spi != -1  ORDER BY spi DESC  LIMIT 30
   ▼
PlateSpiRepository.find_board_series(board_ids, days=100)
   │  每板块取最近100交易日 SPI 时序
   ▼
返回 [{board_id, board_name, spi_now, series:[{date,spi}...]}...]
```

### 10.3 任务流（异步回算，规避 Java 超时陷阱）

```
POST /api/v1/plate-spi/backfill  {start_date}
   ▼
AnalysisTaskQueue.submit_background_task()   ← 复用现有队列，立即返回 task_id（HTTP 202）
   │
   ▼  后台线程（不阻塞 HTTP）
SpiTaskRunner.backfill_history(start_date, end_date)
   │  trading_dates = iter_trading_dates(start_date, end_date)
   │  for current_date in trading_dates:
   │     全量板块并行计算（10.1 写流）
   │     任务进度更新：AnalysisTaskQueue.update_progress(done/total)
   ▼
GET /api/v1/plate-spi/status/{task_id}
   → {status: pending/running/completed/failed, progress: 0.65, done:195/300}
```

---

## 11. 核心逻辑流程图

### 11.1 SPI 单序列计算（算法核心，复刻 `emaForSPI` + `calStockSPI`）

```
                    ┌─────────────────────────────────┐
                    │ 输入: close 序列（阶段1为行业指数）│
                    │      [c0, c1, ..., cN] (N+1根)   │
                    └──────────────┬──────────────────┘
                                   ▼
                    ┌─────────────────────────────────┐
                    │ ema_for_spi(close_series):       │
                    │  for period in [5,13,21,34,      │
                    │                 55,89,144,233]:   │
                    │    if len(series) < period:      │
                    │        continue  ← 数据不足跳过   │
                    │    ema[period] = EMA(close,period)│
                    │                  .iloc[-1]        │
                    └──────────────┬──────────────────┘
                                   ▼
                    ┌─────────────────────────────────┐
                    │ cal_stock_spi(last_close, ema):  │
                    │  spi = 0                         │
                    │  for period, val in ema.items(): │
                    │    if last_close > val:          │
                    │        spi += 1                  │
                    │  return spi   # 0~8 整数          │
                    └─────────────────────────────────┘
```

> EMA 实现：`ewm(adjust=False)` + 手写 SMA warm-up 复刻 TA-Lib seed 语义。
> 阶段1实际输入为**行业指数 close 序列**，算法仍复用 `ema_for_spi` / `cal_stock_spi` 的单序列判定。

### 11.2 板块 SPI 计算（并行编排）

```
        ┌──────────────────────────────────────────┐
        │ cal_index_spi(board_id, trade_date):      │
        └────────────────────┬─────────────────────┘
                             ▼
        ┌──────────────────────────────────────────┐
        │ kline = AkshareSwAdapter.get_index_kline( │
        │          board_id, end_date=trade_date)   │
        └────────────────────┬─────────────────────┘
                             ▼
                   ┌─────────┴─────────┐
                   │ K线为空/不足?      │
                   └────┬────────┬──────┘
                       YES      NO
                        │        │
                        ▼        ▼
                   return -1   ┌────────────────────────────┐
                  (哨兵)       │ ema_for_spi(kline.close)    │
                               │ cal_stock_spi(last_close)   │
                               │ coverage=len/233            │
                               │ upsert_snapshot(...)        │
                               └────────────────────────────┘
```

### 11.3 时间锚点（`spi_time`，复刻 `CommonUtils.spiTime`）

```
            输入: now (当前时刻)
                    │
                    ▼
        flag = 当日 15:00:00
                    │
            ┌───────┴────────┐
        now ≥ flag?           │
            │                 │
          YES                NO
            │                 │
            ▼                 ▼
      anchor = flag      anchor = 前一交易日 15:00
      (当日收盘点)        (最近已收盘交易日)
                    │
                    ▼
            SPI 锚定到 anchor
       （避免盘中未定型K线污染）
```

### 11.4 历史补算任务（异步状态机，规避 Java 同步超时）

```
POST /backfill ──► submit_background_task ──► 返回 task_id (202)
                          │
                          ▼
                 ┌────────────────────┐
                 │ status: PENDING    │
                 └────────┬───────────┘
                          ▼
                 ┌────────────────────┐
                 │ status: RUNNING    │  ← 进度可查
                 │ while d < end:     │
                 │   并行算全量板块    │
                 │   update_progress  │
                 │   d = 下一交易日    │
                 └────────┬───────────┘
                          │
                ┌─────────┴──────────┐
              完成                   异常
                │                      │
                ▼                      ▼
        status: COMPLETED      status: FAILED (记录错误)
        progress: 1.0          GET /status 可见错误信息
```

---

## 12. 阶段 1 任务分解（WBS）

| ID | 任务 | 依赖 | 验收（远端容器） |
|---|---|---|---|
| **1.0** | `akshare` 申万一级行业指数契约探查：确认 `sw_index_first_info` / `index_hist_sw` 字段、代码格式、日期顺序 | 无 | 返回 31 个一级行业；字段映射为 `board_id/board_name/date/close` |
| **1.1** | `AkshareSwAdapter`：实现 `get_sw_first_levels` / `get_index_kline(end_date=anchor_date)`，不注册进 `DataFetcherManager` | 1.0 | `pytest test_akshare_sw_adapter.py` 通过 |
| **1.2** | `storage.py` 加 `PlateSpiSnapshot`（board_id,board_name,trade_date,spi,UNIQUE）+ `SpiBackfillTaskRun` 表 | 无 | 建表成功，`Base.metadata.create_all()` 可用 |
| **1.3** | `SpiCalculator` 纯算法：`ema_for_spi`/`cal_stock_spi`/`cal_index_spi` | 无 | `pytest test_spi_calculator.py`（mock close 序列，断言 0~8） |
| **1.4** | `PlateSpiRepository`：upsert（幂等）/ find_top_boards / find_board_series | 1.2 | upsert 重复不产生重复行；排名过滤 -1 |
| **1.5** | `PlateSpiService`：单板块指数 SPI 计算 + 全量并行（ThreadPool）+ `spi_time` 锚点 + coverage 语义 | 1.1,1.3,1.4 | 单板块 E2E：落库一条非 -1 快照 |
| **1.6** | 异步任务接入：`SpiTaskRunner.backfill_history` 提交 `AnalysisTaskQueue` + 交易日进度更新 + `SpiBackfillTaskRun` 元数据记录 | 1.5 | backfill 返回 task_id，status 推进至 COMPLETED |
| **1.7** | API 端点 `plate_spi.py`：`/refresh` `/backfill` `/status/{task_id}` `/rankings` | 1.5,1.6 | curl 4 端点，rankings 返回 Top 30 × 100 日 |
| **1.8** | `main.py` 在 `scheduled_task()` 主分析后追加 SPI 日终刷新（异常隔离） | 1.6 | 定时触发，日志可见 |
| **1.9** | 本地/远端验收：`py_compile` + SPI 定向 pytest + API 端点 + 快照表检查 | 1.1-1.8 | 全流程通过，符合 CLAUDE.md §5.3 |

---

## 13. 关键设计决策

> ⚠️ 本节已经 §29 多模型审查修订：D1/D3/D7 被覆盖（删除线标注），新增 D9–D13。**以本表为准**，§29 为变更说明。

| # | 决策 | 依据 |
|---|---|---|
| D1 | **阶段1按行业指数直算 SPI**：直接读取申万一级行业指数历史日线，不再走“成分股逐股 K 线 + 均值聚合”与 `StockDaily` 本地回填链路 | 当前实现已切换为 `akshare` 行业指数路径，复杂度与请求量显著下降 |
| D2 | **板块级并行**（ThreadPoolExecutor，非进程池） | SPI 是 IO 密集（拉 K 线），线程池够用；用户 S10=A |
| D3 | ~~EMA 用 `ewm(adjust=False)` 等价 TA-Lib~~ **【§29 C-3 修订】** → **EMA 用 `ewm(adjust=False)` + 手写 SMA warm-up 复刻 TA-Lib seed 语义**；编码前以 Java 5/13/233 输出做黄金样例逐值对表，对齐才算迁移成功 | TA-Lib 前 N 期用 SMA seed，pandas 默认首点 seed，长周期(144/233)有偏差（Codex 实证 `Indicators.java:68`） |
| D4 | **`-1` 哨兵保留**：无可用行业指数日线/历史不足无法算出 SPI 时写 `-1`，查询层过滤 | 保持阶段1无效快照语义稳定 |
| D5 | **upsert 幂等**：(board_id,trade_date) UNIQUE 约束 | S7=A，回算可重跑 |
| D6 | **异步任务复用 `AnalysisTaskQueue`**（不新建队列） | S6=A，规避 Java 同步超时陷阱（A3） |
| D7 | **SPI 私有 adapter**：`AkshareSwAdapter` 不接入 `BaseFetcher` / `DataFetcherManager`，SPI 链路保持零侵入 | 避免影响既有 provider 选择与板块能力调用方 |
| D8 | **申万行业标识统一为 6 位 `board_id`**（去掉 `.SI` 后缀），并同步持久化 `board_name` | 当前实现字段规范：`801010.SI` → `801010` |
| **D9** | **`spi_time` 用现成 `get_effective_trading_date`**（交易日语义，非复刻 Java 前一自然日） | H-2：DSA 已有 `trading_calendar.py:196`；有意修正 Java 不严谨的日历处理 |
| **D10** | **排名查询按 anchor 日期**（非 Java `findMostLimit` 的 latest batch 语义） | H-4：Java 忽略传入 date 只取 latest（`PlateElementDayValueServiceI.java:27/34`），DSA 按 anchor 更正确，有意修正 |
| **D11** | **单一并发层**：回算任务内部 ThreadPool（板块级，上限 `min(32,cpu*4)` 实测调整），队列层串行提交 | H-5：避免队列 3 worker × 任务内 ThreadPool 的两级并发失控（`task_queue.py:174/208`） |
| **D12** | **coverage 阈值**：行业指数可用 K 线 < 233 根时 SPI 标记低置信度；≥233 根为 normal | 当前实现的 coverage 已从“成分股覆盖率”改为“K 线长度覆盖率” |
| **D13** | **point-in-time 语义 = 指数 K 线 point-in-time**：历史 SPI 以 `anchor_date` 截止的行业指数 close 序列计算；阶段1不再承担成分股锁定语义 | 与当前 direct-index SPI 实现一致；成分股 point-in-time 下沉到阶段2/3 |

---

## 14. 待确认（规划阶段）

| # | 问题 | 建议默认 |
|---|---|---|
| C1 | 选股宝接口是否需要鉴权（cookie/token）？Java `RestTemplate` 未见显式鉴权 | 默认无鉴权（公开接口），1.0 任务实测确认；若需则补 header |
| C2 | 并行线程数上限？ | 默认 `min(32, cpu*4)`，1.6 任务中按选股宝限流实测调整 |
| C3 | `PlateSpiSnapshot` 是否需要存成分股快照（为阶段2 point-in-time 铺路）？ | **建议存**（board_id,trade_date,constituents_json），阶段2 S2 直接用 |

> 本规划不含 SPI v2 增强、轮动闭环、比价系统（阶段2/3）。

---

**规划置信度**：High。目录/数据流/算法均来自 DSA 现有范式 + Java 源码逐行追溯，无推测。
**唯一实施风险**：C4（`akshare` 行业指数契约与环境可达性），由任务 1.0 在编码前实测消解。

---
---

# 阶段 2 详细规划：SPI 板块轮动改造优化（SPI v2 + 轮动闭环）

> 依据：会话 `e1883821` 调研结论 + Java `Indicators.emaForSPI`/`SourceBar`（已确认 Java 侧 v2 **未实现**，纯新增）+
> DSA 现有 `ewm(adjust=False)` 范式（`stock_analyzer.py`/`alert_indicators.py`）+ YAML 策略范式（`strategies/*.yaml`）。
> **前置依赖**：阶段1 全部产出（行业指数适配层、SPI 快照表、异步任务、`PlateSpiSnapshot`）。

---

## 15. 阶段 2 结构目录（在阶段1基础上扩展）

```
daily_stock_analysis/
├── src/
│   ├── storage.py                  【新增 2 表】PlateSpiSnapshot 扩展 v2 字段列 / SpiRotationSignal（轮动信号）
│   │                                  ※ 用户拒绝前向兼容预留(X6)，故 v2 字段直接 ALTER TABLE 加列
│   │
│   ├── services/
│   │   ├── spi_calculator.py       【改】ema_for_spi 升级为返回 EMA 序列（供方向/排列/分离度计算）
│   │   │                              新增 cal_stock_spi_v2()：调用因子链
│   │   ├── spi_factors/            【新建目录】可插拔因子模块（S17）
│   │   │   ├── __init__.py             因子注册表（registry）
│   │   │   ├── base.py                 SpiFactor 抽象基类：name/weight/compute(ema_series, bars)->float
│   │   │   ├── ema_direction.py        因子1：EMA 自身方向（ema[-1] vs ema[-lookback]）
│   │   │   ├── alignment.py            因子2：均线多头排列有序度（5>13>...>233 计数 0~7）
│   │   │   ├── separation.py           因子3：短期组/长期组分离度（百分比）
│   │   │   └── compression.py          因子4：8条EMA标准差压缩/扩张（变化率）
│   │   ├── spi_scorer.py           【新建】v2 打分引擎：加载因子registry → 加权/乘积组合 → 归一化到[0,100]
│   │   └── rotation_service.py     【新建】轮动闭环：观察池/入场信号/出场信号
│   │
│   ├── repositories/
│   │   └── plate_spi_repo.py       【改】新增 v2 快照读写 + 轮动信号 CRUD
│   │
│   └── utils/
│       └── constituents_snapshot.py【新建】point-in-time 成分股锁定（S2）：按交易日存历史成分股名单
│
├── api/v1/endpoints/
│   └── plate_spi.py                【改】新增 GET /rotation/signals（轮动信号查询）/ GET /v2/rankings
│
├── strategies/                     【新增 1 配置】rotation_entry.yaml（回踩入场规则，复用 YAML 策略范式）
│
└── tests/
    ├── test_spi_factors.py         【新建】4 因子纯算法单测（mock ema_series）
    ├── test_spi_scorer.py          【新建】打分组合 + 权重 + 归一化 单测
    └── test_rotation_service.py    【新建】入场/出场信号触发 + 观察池进出 单测
```

**改动统计**：新建 11 文件，改 4 处。**仍零侵入** `get_sector_rankings`/`get_belong_boards`/`capital_flow_context`。

---

## 16. SPI v2 因子数据流

```
成分股日K线 (close/high/low 序列；当前可通过 AlphaSift 成分股列表 + DSA 日线链路获取)
   │
   ▼
ema_for_spi_v2(close_series)
   │  返回 EMA 序列（非末位标量），供方向/排列/分离度/压缩计算
   │  {5:[...], 13:[...], ..., 233:[...]}
   ▼
┌────────────────────────────────────────────────────┐
│  4 个可插拔因子并行计算（SpiFactor.compute）:        │
│                                                     │
│  F1 ema_direction:  各周期 ema[-1] vs ema[-5]       │
│                     上升周期数 / 8        → [0,1]    │
│  F2 alignment:      5>13>21>...>233 满足对数        │
│                     / 7                → [0,1]      │
│  F3 separation:     (short_avg-long_avg)/long_avg   │
│                     归一化              → [0,1]     │
│  F4 compression:    std(ema_now) vs std(ema_prev)   │
│                     扩张为正，压缩为负   → [-1,1]   │
└────────────────────────────────────────────────────┘
   │
   ▼
SpiScorer.score(factors, weights)
   │  组合方式（C2 待定，默认加权和）:
   │    v2_score = Σ(weight_i × factor_i)
   │  归一化到 [0, 100]
   ▼
cal_stock_spi_v2 单股 v2 分 → 板块聚合（阶段2 自建成分股编排，不再依赖阶段1 `cal_board_spi`）
   ▼
PlateSpiSnapshot.v2_score 列（ALTER TABLE 加列）
```

> 基于已实现 phase1：**若阶段2先只落地板块级 `v2_score`**，可直接复用 phase1 已有的申万一级行业指数 K 线，不必等待 `2.4 constituents_snapshot`。
> 成分股链路只在 **轮动闭环（观察池/入场/出场）** 和后续 **板块内选股** 中成为硬依赖。

---

## 17. v2 因子核心逻辑（4 因子流程图）

### 17.1 单股 v2 打分

```
            ┌──────────────────────────────────┐
            │ 输入: ema_series (8周期各一条)    │
            │      + last_close                │
            └──────────────┬───────────────────┘
                           ▼
   ┌────────────────── 并行 ───────────────────┐
   │                                            │
   ▼            ▼              ▼              ▼
 F1方向      F2排列         F3分离度       F4压缩扩张
 (ema斜率)   (有序对数)     (短长期距)     (std变化率)
   │            │              │              │
   └────────────┴──────────────┴──────────────┘
                           │
                           ▼
            ┌──────────────────────────────────┐
            │ SpiScorer: 加权组合 + 归一化[0,100]│
            │  权重来源: 配置文件/经验值(S17可调) │
            └──────────────────────────────────┘
                           │
                           ▼
                  单股 v2_score (连续值)
```

### 17.2 因子可插拔（S17）注册机制

```
spi_factors/__init__.py
   REGISTRY = { "ema_direction": EmaDirectionFactor(weight=0.3),
                "alignment":     AlignmentFactor(weight=0.3),
                "separation":    SeparationFactor(weight=0.25),
                "compression":   CompressionFactor(weight=0.15) }

SpiScorer.score():
   for name, factor in REGISTRY.items():
       factors[name] = factor.compute(ema_series, bars)
   return combine(factors, weights)
   ※ 增删因子 = 改 REGISTRY，不动 scorer 主逻辑
```

---

## 18. 板块轮动闭环数据流

```
每日盘后（复用阶段1日终任务时序）
   │
   ▼
┌─────────────────────────────────────────────────┐
│ Step1 排名预选：v2_score Top N → 观察池          │
│   (N 可配，默认复用 PLATE_MOST_LIMIT=30)         │
└──────────────────────┬──────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────┐
│ Step2 成分股 point-in-time 锁定（S2）:           │
│   constituents_snapshot(board_id, trade_date)    │
│   → 该交易日真实成分股名单（防前视）              │
└──────────────────────┬──────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────┐
│ Step3 入场信号（回踩均线，rotation_entry.yaml）:  │
│   板块内个股 回踩至 10/20日EMA 支撑 + 量能确认    │
│   → 触发 BUY 信号                                │
└──────────────────────┬──────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────┐
│ Step4 出场信号:                                  │
│   板块 v2 排名跌出 Top M  或  个股跌破 N日EMA     │
│   → 触发 SELL 信号                               │
└──────────────────────┬──────────────────────────┘
                       ▼
            SpiRotationSignal 表 (storage.py)
            (board_id, stock_code, trade_date, action=BUY/SELL, reason)
                       │
                       ▼
            GET /api/v1/plate-spi/rotation/signals
```

### 18.1 轮动信号状态机

```
        观察池外
            │ v2 进入 Top N
            ▼
        ┌────────┐  回踩均线+量能  ┌──────┐
        │ WATCH  │ ──────────────► │ BUY  │
        └────┬───┘                 └──┬───┘
             │ 跌出 Top N             │ 跌破EMA / 板块跌出Top M
             ▼ 退出观察              ▼
        (移除)                    ┌───────┐
                                  │ SELL  │
                                  └───────┘
```

> **会话 e1883821 共识**：轮动闭环"先作观察/提示能力，再决定是否直连策略"。阶段2 仅产出信号落库 + 查询接口，**不自动下单**。

---

## 19. 阶段 2 任务分解（WBS）

| ID | 任务 | 依赖 | 验收 |
|---|---|---|---|
| **2.0** | `storage.py` ALTER：`PlateSpiSnapshot` 加 v2_score 等列；新建 `SpiRotationSignal` 表 + 成分股快照表 | 阶段1 | 建表/加列成功 |
| **2.1** | `spi_factors/` 4 因子模块 + registry（S17 可插拔） | 无 | `pytest test_spi_factors.py` 各因子输出范围正确 |
| **2.2** | `SpiScorer` 打分引擎（加权和默认 + 归一化） | 2.1 | `pytest test_spi_scorer.py` 权重变更反映到分数 |
| **2.3** | `spi_calculator` 升级 `ema_for_spi` 返回序列 + `cal_stock_spi_v2` 接入因子链 | 2.1,2.2 | v2 板块快照落库，v2_score ∈ [0,100] |
| **2.4** | `constituents_snapshot` point-in-time 成分股锁定（S2） | 现有 AlphaSift 成分股链路 | 从启用日开始按交易日落盘真实名单；旧历史日不承诺回补真实名单 |
| **2.5** | `rotation_service`：观察池 + 入场（回踩）+ 出场信号 | 2.3,2.4 | `pytest test_rotation_service.py` 信号触发正确 |
| **2.6** | `rotation_entry.yaml` 策略配置（回踩均线参数可调） | 2.5 | YAML 加载，参数生效 |
| **2.7** | API：`/rotation/signals` `/v2/rankings` | 2.5 | `v2/rankings` 可先独立上线；`rotation/signals` 随 2.5 一起验收 |
| **2.8** | 日终任务扩展：v2 计算 + 轮动信号生成并入阶段1日终流程 | 2.3,2.5 | 日终至少稳定产出 v2 快照；信号生成取决于 2.4 成分股快照已落盘 |
| **2.9** | 远端验收：挂载重启 → curl + 查信号表 | 2.0-2.8 | 全流程通过 |

---

## 19.1 阶段 2 数据源可达性审计（基于已实现 phase1）

| 任务 | 所需数据源 | 当前状态 | 说明 |
|---|---|---|---|
| **2.1 / 2.2** 因子模块 / 打分器 | 无新增外部数据源 | **已具备** | 纯计算层，直接基于 `ema_for_spi` 输出扩展 |
| **2.3** v2 板块评分 | phase1 行业指数历史 K 线 | **已具备** | phase1 已有 `AkshareSwAdapter.get_index_kline`；实测可返回 `801010` 最近 10 根指数 K 线 |
| **2.4** 成分股快照 | 板块/题材 → 成分股列表 | **部分具备** | 当前有 AlphaSift 概念/行业成分股链路；概念题材实测可返回多只成分股，但申万一级行业名与现有成分股源命名体系不完全对齐，部分仅退化到单只龙头兜底 |
| **2.4** 历史 point-in-time | 历史日真实成分股名单 | **未具备（仅可从启用日开始积累）** | 现有链路只能稳定拿到“当前快照”；历史真实名单需由阶段2 自建 `constituents_snapshot` 每日落盘 |
| **2.5** 轮动信号 | 成分股列表 + 成分股日 K + v2 评分 | **部分具备** | 成分股个股日 K 可通过 DSA 现有 `get_daily_history` / `StockRepository` 获取；当前日或启用后日可做，旧历史回放依赖 2.4 快照积累 |
| **2.7** `v2/rankings` | phase1 指数 SPI / v2_score | **已具备** | 不依赖成分股快照，可先上线 |
| **2.7** `rotation/signals` | 2.5 轮动信号结果 | **部分具备** | 取决于 2.4 / 2.5 是否完成 |
| **2.8** 日终任务扩展 | 指数 K 线 + （可选）成分股快照 | **部分具备** | v2 快照部分可先接入；轮动信号部分需等 2.4/2.5 完整闭环 |

## 20. 阶段 2 设计决策

| # | 决策 | 依据 |
|---|---|---|
| D2.1 | **v2 字段直接 ALTER TABLE 加列**（不做前向兼容预留） | 用户 X6=R 拒绝 S16 |
| D2.2 | **因子可插拔 registry** | S17=A；增删因子改注册表，不动 scorer |
| D2.3 | **打分默认加权和**（非乘积/分段），权重配置化 | S1=A；C2 待定，默认最简；S17 保证可调 |
| D2.4 | **EMA 序列化**：`ema_for_spi` 从返回末位标量升级为返回完整序列 | v2 因子（方向/排列/分离度）需多周期多点 EMA |
| D2.5 | **轮动仅产信号，不自动交易** | 会话 e1883821 共识"先观察后策略" |
| D2.6 | **point-in-time 成分股快照独立表** | S2=A；阶段1 C3 若已存则复用，否则新建 |

---

## 21. 阶段 2 待确认

| # | 问题 | 建议默认 |
|---|---|---|
| C2.1 | v2 打分组合方式（加权和 / 乘积 / 分段）？ | 默认加权和；会话评审弱点1指出权重需量化，但用户 X(S18)=R 不做回测，故经验值 + 可调 |
| C2.2 | 入场"回踩均线"具体参数（10日 or 20日？量能阈值？）？ | 默认 20日EMA + 量比>1.2，写入 `rotation_entry.yaml` 可调 |
| C2.3 | 出场"排名跌出 Top M" 的 M 值？ | 默认 M=50（宽于观察池 N=30，避免频繁进出） |

---
---

# 阶段 3 详细规划：比价系统

> 依据：会话 `e1883821` I4 + 调研3（CMF/资金流）+ DSA `capital_flow_context`(个股级 stock_flow) +
> `StockRepository.get_range`(历史日线含 volume) + 阶段1板块 SPI/行业指数基础 + 阶段2 point-in-time 快照。
> **前置依赖**：阶段1（板块 SPI 与行业指数基础）、阶段2（point-in-time 成分股快照）。

---

## 22. 阶段 3 结构目录

```
daily_stock_analysis/
├── src/
│   ├── storage.py                  【新增 2 表】PricingSnapshot（比价快照）/ PricingFactorRun（因子计算记录）
│   │
│   ├── services/
│   │   ├── pricing/                【新建目录】比价系统
│   │   │   ├── __init__.py
│   │   │   ├── relative_strength.py 个股相对强弱（vs 板块均值 / vs 板块指数）
│   │   │   ├── cmf.py              Chaikin Money Flow 因子（复用 K线 high/low/close/volume）
│   │   │   └── capital_proxy.py    资金流代理因子（适配 capital_flow_context 的 stock_flow）
│   │   └── pricing_service.py      【新建】比价编排：板块内个股比价 + 快照 + 排名
│   │
│   ├── repositories/
│   │   └── pricing_repo.py         【新建】PricingSnapshot CRUD + 板块内个股比价排名查询
│   │
│   └── schemas/
│       └── pricing.py              【新建】比价结果 Schema（板块内个股排序 + 因子值）
│
├── api/v1/endpoints/
│   └── plate_pricing.py            【新建】GET /board/{board_id}/pricing（板块内个股比价排名）
│
└── tests/
    ├── test_cmf.py                 【新建】CMF 算法单测（mock K线）
    ├── test_relative_strength.py   【新建】相对强弱单测
    └── test_pricing_service.py     【新建】板块内比价编排 + 快照 单测
```

**改动统计**：新建 9 文件，改 1 处（storage.py 加表）。**零侵入**现有三函数（`capital_flow_context` 只读适配）。

---

## 23. 比价系统数据流

```
板块成分股（legulegu current snapshot / 阶段2 point-in-time 快照）
   │   ※ 实现为 src/utils/constituents_snapshot.py 的 ConstituentFetcher(legulegu)
   ▼  对每个成分股
┌──────────────────────── 并行 ────────────────────────┐
│                                                       │
│ ▼ 相对强弱                ▼ CMF                      ▼ 资金流代理
│ relative_strength.py      cmf.py                     capital_proxy.py
│  个股20日收益              CMF(20):                    适配 capital_flow_context
│  板块内百分位 rank          Σ[((c-l)-(h-c))/(h-l)×vol]  .stock_flow 真实字段
│  → rs_score [0,1]         /Σvol → [-1,1]              main_net_inflow 等
│                            → cmf_score=(cmf+1)/2       → flow_score [0,1]
│                            ∈[0,1]                       ※ 仅当日有效，历史日整批 None
└───────────────────────────┬───────────────────────────┘
                            ▼
              pricing_service.combine()  ※ 批次级（板块-交易日）动态重归一化
                尺度统一后: effective_w_i = base_w_i / Σ(base_w_active)
                base: RS0.5/CMF0.3/Flow0.2；Flow 整批缺失→RS0.625/CMF0.375
                RS 或 CMF 缺 → 该股 total=degraded（不参与排名）
                比价综合分 = Σ(effective_w_i × factor_i) ∈ [0,1]
                            ▼
              板块内个股比价排名
                P0: same-day（current snapshot）
                P1: point-in-time（仅 snapshot 启用日起的历史回放）
                            ▼
              PricingSnapshot 表 (board_id, stock_code, trade_date,
                                  rs_score, cmf[-1,1], flow_score, total[0,1],
                                  factor_mask, run_id)
              PricingFactorRun 表 (board_id, trade_date, constituent_basis,
                                  base_weights_json, effective_weights_json,
                                  flow_coverage, ...)  ※ 无 UNIQUE，保留多 rerun
                            ▼
              GET /api/v1/plate-pricing/board/{id}/pricing
                → 板块内个股强弱排序（选股辅助）
```

> **尺度统一约束（DR4）**：CMF 原始 ∈[-1,1] 必须经 `cmf_score=(cmf+1)/2` 映射到 [0,1] 后方可与 RS/Flow 加权，禁止异尺度直接相加。
> **重归一化粒度（DR-批次）**：按"板块-交易日"批次判定 Flow 可用性（flow_coverage 阈值），整批禁用或整批启用，避免同榜单不同权重破坏比较性。

### 23.1 比价能力分层（防前视承诺拆分）

> **DR6 修订**：原 §23.1 写法像"历史任意日 D 可无前视回放"，与 §25.1 "仅启用日起积累"矛盾。拆为两条能力声明：

```
能力 A — same-day ranking（P0，phase2 未收敛也可交付）:
   constituents = legulegu current snapshot  ← 当日实时成分股
   for stock in constituents:
       kline = K线截至(今日)
       rs/cmf/flow 仅基于 ≤今日 的信息
   → PricingSnapshot(今日)
   ※ current snapshot 含生存者偏差，仅用于当日选股辅助，不承诺历史无前视

能力 B — historical point-in-time replay（P1，依赖 phase2 成分股每日落盘闭环）:
   constituents = ConstituentSnapshot(D)  ← 锁定 D 日真实成分股
   for stock in constituents:
       kline = K线截至(D)  ← 仅用 D 及之前数据
   → PricingSnapshot(D)  无前视偏差
   ※ 仅对 phase2 save_constituents 启用日及之后的 D 有效；旧历史日不承诺
```

---

## 24. 比价因子核心逻辑

### 24.1 CMF（Chaikin Money Flow）— 主资金流因子

```
输入: 20日 K线 [(high, low, close, volume)...]
   │   ※ 窗口=20（项目默认）。业界口径：StockCharts/pandas_ta 默认20，
   │      TradingView "通常20或21"，Fidelity 用21；20 更主流，21 是变体，非唯一标准。
   ▼
for each day:
   mf_multiplier = ((close-low)-(high-close)) / (high-low)
   mf_volume = mf_multiplier × volume
   │   (high==low 时 mf_multiplier=0，避免除零，不缩短窗口)
   ▼
CMF(20) = Σ(mf_volume over 20日) / Σ(volume over 20日)
   │   (Σvolume == 0 时返回 None，非 0：None=因子未定义，0=中性会污染总分)
   ▼
CMF ∈ [-1, 1]
   >0 资金流入（积聚），<0 资金流出（派发）
   ※ 进入加权和前必须 cmf_score=(cmf+1)/2 映射到 [0,1]
```

> 数据源：`StockRepository.get_range()` 取个股 20 日 K 线（含 volume，本地 StockDaily 已有）；实测 `get_dsa_daily_history("002623")` 可返回 41 根日线并带 `volume`。
> 复权一致性：H/L/C 必须同一前复权(qfq)；StockDaily 按(code,date) upsert，phase3 须固定沿用同一日线链路，避免跨 provider 混写导致窗口价量基准漂移。
> 边界（DR-漏项）：K 线不足 20 根时按实际可用 N 计算（min_period=5）；N<5 → CMF=None。新股、停牌、零成交量日均按上述除零/None 规则处理，不抛异常。
> 会话调研3：CMF 叠加动量可提升 IR 20-27bp/月。

### 24.2 相对强弱（RS）

```
RS_raw = 个股20日收益 = close_t / close_{t-20} - 1
归一化: 板块内百分位 rank
   rs_score = (rank_avg - 1) / (n - 1)   ∈ [0,1]
   n <= 1 → rs_score = 0.5
→ 衡量个股在板块内的相对强弱排序
```

> **DR5 修订（关键）**：原写法 `个股收益 - 板块指数收益`。但在"板块内 rank 百分位"框架下，同板块同日的板块指数收益对全部个股是常数，**减与不减不改变名次**——即"减板块指数"对排序结果数学上不生效。故 RS 排序输入直接用 `个股20日收益`；`excess_return = 个股收益 - 板块指数收益` 降级为**诊断字段**（保留供分析，不进排序）。
> **DR8**：N=20，与 CMF(20) 窗口统一。
> 板块指数基准序列仍由 phase1 `AkshareSwAdapter.get_index_kline` 提供，仅用于 excess_return 诊断，不作为 RS 排序输入。

### 24.3 资金流代理（适配现有 `capital_flow_context`）

```
capital_flow_context(stock).stock_flow
   = {main_net_inflow, inflow_5d, inflow_10d}    ※ DR3：真实字段（fundamental_adapter.py:442）
   │                                              原 §24.3 旧字段 super_large_net/large_net 已废弃
   ▼
capital_proxy.extract(stock_flow)
   主力净流入代理 = main_net_inflow
   归一化: 当日板块内百分位 rank → flow_score ∈ [0,1]
→ Flow_Score
   ※ capital_flow_context 是个股级、非历史序列（实时/近端快照）
   ※ 仅作辅助因子，非主链路（会话明确"非北向资金替代品"）
   ※ fail-open 降级（DR-批次）:
      - 单只失败 → flow_score = None（禁止塞 0.5，0.5 会把"无数据"伪装成"中性"）
      - 批次级（板块-交易日）flow_coverage < 阈值 → 整批禁用 Flow，对 RS/CMF 动态重归一化
      - 历史日回算: capital_flow_context 无历史序列，历史日整批 Flow=None
```

---

## 25. 阶段 3 任务分解（WBS）

> P0/P1 分层：3.1–3.7 属 P0（same-day current snapshot，phase2 未收敛可交付）；历史 point-in-time 回放属 P1（依赖 phase2 成分股每日落盘闭环）。

| ID | 任务 | 依赖 | 验收 |
|---|---|---|---|
| **3.0** | 冻结口径：修订需求文档 §22–§28（DR1–DR10 + 漏项），使 CMF 窗口/RS 口径/Flow 字段/尺度统一/批次重归一化自洽 | 无 | 文档无内部矛盾，命令/文件名与实现一致 |
| **3.1** | `cmf.py`：CMF(20)，high==low→0、Σvol=0→None、复权固定、min_period=5 | 3.0 | `pytest test_cmf.py` 停牌/零量/不足N 不抛错，None 语义正确 |
| **3.2** | `relative_strength.py`：20日收益板块内百分位（不减指数），n<=1→0.5 | 3.0 | `pytest test_relative_strength.py` rank∈[0,1] |
| **3.3** | `capital_proxy.py`：适配 main_net_inflow 等真实字段（只读），fail-open→None | 3.0 | 断网不阻断，返回 None |
| **3.4** | `pricing_service.py`：批次级动态重归一化 + cmf_score=(cmf+1)/2 + RS/CMF 缺→degraded | 3.1-3.3 | Flow 整批禁用时 RS0.625/CMF0.375；RS/CMF 缺→degraded |
| **3.5** | `pricing_repo.py`：PricingSnapshot upsert + PricingFactorRun（无 UNIQUE，多 rerun）+ 板块内排名查询 | 3.0 | upsert 幂等；排名返回排序；run 可追溯权重 |
| **3.6** | 成分股读路径：`get_constituents` 缺失时 fallback latest（phase2 收敛后自动升级 point-in-time，无需改码） | 3.4 | 历史日查不到→返回 latest，链路打通 |
| **3.7** | API `plate_pricing.py` `GET /board/{id}/pricing` + 日终接入 `refresh_daily` 链尾独立 try/except | 3.4-3.6 | curl 返回板块内排名；日终异常隔离不拖垮 v1/v2/rotation 主链 |
| **3.8** | 远端验收：挂载重启 → sqlite 查表 + curl + 日志 grep | 3.0-3.7 | 同日产比价快照、Flow 失效不压低总分、历史回放声明与真实能力一致 |

---

## 25.1 阶段 3 数据源可达性审计（基于已实现 phase1）

| 任务 | 所需数据源 | 当前状态 | 说明 |
|---|---|---|---|
| **3.1** CMF | 个股 20 日 OHLCV | **已具备** | 复用 `StockRepository.get_range` / DSA 日线链路；实测成分股 `002623` 可返回带 `volume` 的日线数据 |
| **3.2** RS | 个股日 K（板块指数仅做诊断） | **已具备** | 个股日线已具备；板块指数基准由 phase1 `AkshareSwAdapter.get_index_kline` 提供，仅用于 excess_return 诊断字段，不进 RS 排序 |
| **3.3** Flow | `capital_flow_context.stock_flow` | **部分具备** | 真实字段 `main_net_inflow` / `inflow_5d` / `inflow_10d`；实时/近端快照非历史序列；当前环境实测 fail-open `status=failed`，仅当日 best-effort，不作为历史主链路 |
| **3.4** 比价快照 | 成分股快照 + 3.1/3.2 + （可选）3.3 | **部分具备** | **P0**：legulegu current snapshot 可做 same-day 排名；**P1**：phase2 `ConstituentSnapshot` 表+repo 已存在，但 `refresh_daily` 未接入 `save_constituents`（phase2 blocking gap），历史 point-in-time 回放需待其收敛 |
| **3.6 / 3.7** API / 日终接入 | 3.4 产物 | **部分具备** | 先以 `RS + CMF` 形成主链路，`Flow` 作为 best-effort 增强 |

## 26. 阶段 3 设计决策

| # | 决策 | 依据 |
|---|---|---|
| D3.1 | **CMF 为主资金流因子**（用 K 线 volume，历史可得） | 调研3：CMF 可准实时；ETF 资金流 T+1 滞后 |
| D3.2 | **`capital_flow_context` 只读适配为辅助因子（best-effort）** | 用户明确"非北向资金替代品"；且它是实时快照、非历史序列，不应成为比价历史回算硬依赖 |
| D3.3 | **比价 = 板块内口径**（首期不做跨板块比价） | 会话 I4 明确"板块内个股比价为首期口径" |
| D3.4 | **point-in-time 复用阶段2成分股快照** | S2=A；阶段2已建，避免重复 |
| D3.5 | **不涉北向资金**（X2 排除） | 用户明确"暂不考虑" |
| **D3.6** | **CMF 窗口=20，RS 窗口=20**（项目默认，非业界唯一标准） | DR1：StockCharts/pandas_ta 默认20，21 是变体；窗口统一便于对齐 |
| **D3.7** | **因子尺度统一后再加权**：`cmf_score=(cmf+1)/2` | DR4：禁止 CMF[-1,1] 与 RS/Flow[0,1] 异尺度直接相加 |
| **D3.8** | **动态重归一化按"板块-交易日"批次发生**；RS/CMF 缺→degraded | DR-批次：避免同榜单不同权重破坏比较性；RS/CMF 为核心因子，缺一不补 |
| **D3.9** | **能力分层 P0/P1**：P0 same-day current snapshot；P1 历史 point-in-time 回放 | DR6：拆分承诺，避免过度承诺历史无前视 |

---

## 27. 阶段 3 待确认

| # | 问题 | 建议默认 |
|---|---|---|
| C3.1 | 比价综合分权重（RS / CMF / Flow）？ | 默认 0.5 / 0.3 / 0.2，配置化可调；批次级缺失时按 D3.8 动态重归一化 |
| C3.2 | ~~相对强弱基准：板块指数 vs 板块成分均值？~~ **【DR5 已澄清】** | 板块内 rank 百分位框架下，"减板块指数"对排序**数学上不生效**（同板块同日是常数）。RS 排序直接用个股20日收益；板块指数降级为 excess_return **诊断字段**。本项不再作为待确认 |
| C3.3 | 比价快照保留周期（S9）？ | 默认全保留（磁盘便宜），后续按需冷热分层 |
| C3.4 | Flow 批次级 flow_coverage 阈值？ | 默认 0.6（低于则整批禁用 Flow，对 RS/CMF 重归一化）；可配置 |

---

## 28. 三阶段总览（依赖链 + 复用）

```
阶段1 (SPI v1 迁移)
  ├─ 行业指数适配层 (`akshare`) ────┐
  ├─ SPI 快照表 ────────────────────┤
  ├─ 异步任务队列 ──────────────────┤
  └─ spi_calculator (ema_for_spi) ──┤
                                    ▼
阶段2 (SPI v2 + 轮动闭环)           │ 复用阶段1全部
  ├─ 4 因子 (方向/排列/分离度/压缩) ◄─ spi_calculator 升级
  ├─ point-in-time 成分股快照 ──────┐
  ├─ 轮动信号 (观察池/入场/出场)    │
  └─ v2 打分引擎 ───────────────────┤
                                    ▼
阶段3 (比价系统)                    │ 复用阶段1板块 SPI/指数基础 + 阶段2成分股快照
  ├─ CMF(20) 因子 ◄─ StockRepository.get_range (现有)
  ├─ 相对强弱 RS(20) ◄─ 个股20日收益板块内百分位（板块指数仅做 excess_return 诊断）
  ├─ 资金流代理 Flow ◄─ capital_flow_context (只读适配，当日 best-effort)
  └─ 板块内比价排名 + 快照（P0 same-day / P1 历史 point-in-time）
```

**全周期置信度**：High。三阶段目录/数据流/算法均锚定 DSA 现有范式 + Java 源码 + 会话调研结论。
**累计新建文件**：阶段1 以 `src/services/spi/` 与 `plate_spi` API 为主；现有文件改动集中在 `storage.py`、`main.py`、`plate_spi.py` 与路由聚合，保持对既有业务零侵入。

---

## 28.1 阶段3 双模型规划审计决议（Gemini + Codex，2026-07-03）

> 经 `codeagent-wrapper` 调 Gemini-2.5-pro 与 Codex 双模型独立审计（Codex 118 事件含 web_search 交叉验证 CMF 业界标准 + 逐文件源码定位），
> 双方互补揪出 §22–§28 共 **10 处契约漂移 + 3 处漏项**，已全部回填上文（DR1–DR10）。本节为审计结论归档，对 §22–§28 构成约束性更新。
> 原始产物：`.ccg/tasks/spi-rotation-phase3/research/{gemini,codex}-analysis.md`。

### 28.1.1 契约漂移修订（DR1–DR10）

| # | 漂移 | 修订 | 裁定来源 |
|---|---|---|---|
| DR1 | CMF(21) | **CMF(20)**，标注"项目默认非业界唯一标准" | Codex web_search 4 源（StockCharts/pandas_ta 默认20） |
| DR2 | Σvolume=0 → CMF=0 | **→ None**（未定义而非中性） | Codex |
| DR3 | Flow 旧字段 super_large_net/large_net | **main_net_inflow/inflow_5d/inflow_10d**（fundamental_adapter.py:442） | H-3 + Codex |
| DR4 | CMF[-1,1] 与 RS/Flow[0,1] 直接加权和 | **cmf_score=(cmf+1)/2** 尺度统一后再加权 | Codex |
| DR5 | RS "减板块指数" 进排序 | **rank 框架下不减**（数学冗余）；excess_return 降级诊断字段 | Codex 数学论证 |
| DR6 | §23.1 vs §25.1 历史回放承诺矛盾 | 拆 P0 same-day / P1 历史 point-in-time 两条能力 | Codex |
| DR7 | §23 "AlphaSift 成分股链路" | 实现为 **legulegu**（constituents_snapshot.py:18） | Codex |
| DR8 | RS 窗口 N 未定 | **N=20**（与 CMF 统一） | 综合 |
| DR9 | Flow_norm 口径不清 | 板块内百分位，**仅当日有效；历史日整批 None** | Codex |
| DR10 | WBS 无 rerun 审计 | **PricingFactorRun 表**（无 UNIQUE，存 effective_weights_json） | Codex |

### 28.1.2 新增漏项

- RS/CMF 不足 N、新股、停牌、零成交量、n<=1→0.5 的处理契约（已补 §24.1/§24.2）
- Flow 批次级 flow_coverage 阈值（C3.4，默认 0.6）
- rerun 审计口径（PricingFactorRun，DR10）

### 28.1.3 P0/P1 分层（用户已确认 CMF(20) + 先修文档）

- **P0**（phase2 未收敛可交付）：same-day 板块内比价 = RS(20) + CMF(20) 主链 + Flow 当日 best-effort，legulegu current snapshot 成分股
- **P1**（依赖 phase2 `refresh_daily` 接入 `save_constituents`）：历史 point-in-time 回放（无前视），phase2 收敛后 phase3 无需改码自动升级

### 28.1.4 模型表现差异

Codex 发现 4 个 Gemini 盲点：CMF 业界标准（21 vs 20）、§24.3 字段漂移、RS 减指数数学冗余、Alembic 漂移（DSA 实际用 `Base.metadata.create_all()`）。
Gemini 在表字段设计/伪代码/远端验收清单上有独立复用价值。双模型交叉显著优于单模型。

---
---

# §29 多模型审查结论与方案修订（Gemini + Codex）

> 经 `codeagent-wrapper` 调 Gemini 与 Codex 双模型独立审查（Codex 深度 207 事件含 web_search + 逐文件源码定位），
> 双方**一致给出 Medium 执行置信度**，并互补揪出 4 Critical / 5 High / 7 Medium 问题。
> 用户已就关键争议项拍板，本章为**审查结论 + 修订决议**，对前文 §9–28 构成约束性更新。

---

## 29.1 Critical 问题处置（用户已决策）

| # | 问题 | 两模型判断 | **用户决策 → 修订** |
|---|---|---|---|
| **C-1** | 历史成分股 point-in-time | 早期方案担心 `plate_set` 仅当前快照→生存者偏差，阶段2 无法修复部署前历史 | **阶段1不再以成分股聚合作为 SPI 主链路**。当前实现改为申万一级行业指数直算 SPI，历史回算只需保证**指数 K 线 point-in-time**；成分股 point-in-time 仍保留在阶段2/3 处理 |
| **C-2** | 成分股逐股 K 线请求量过大 | 逐股 300 日 K 线会放大请求量、并引入本地缓存/回填复杂度 | **阶段1改为行业指数直算**。直接读取 `ak.index_hist_sw(symbol, period="day")` 截止 `anchor_date` 的历史日线，移除 `StockDaily` / 选股宝逐股回源依赖 |
| **C-3** | EMA 保真度 | TA-Lib 前 N 期 SMA seed ≠ pandas 首点 seed，长周期(144/233)偏差 | **编码前做黄金样例对表**。→ §12 新增任务 **1.0a**：取 Java 5/13/233 周期输出做逐值对表，定 Python seed/lookback 实现（手写 SMA warm-up 复刻 TA-Lib），对齐后才算迁移成功 |
| **C-4** | 行业指数真实契约 | 申万一级列表、指数代码与历史日线字段需在当前运行环境确认 | **任务 1.0 升级为 `akshare` 契约 smoke**：确认 `sw_index_first_info` 返回 31 个一级行业、`index_hist_sw` 可按代码取历史日线、代码可规范化为 6 位 `board_id` |

## 29.2 High 问题处置（用户已决策 A）

| # | 问题 | Codex 实证 | **修订（采纳）** |
|---|---|---|---|
| **H-1** | 注册全局 fetcher ≠ 零侵入 | SPI 若挂进公共 fetcher 链会污染既有 provider 选择 | **行业指数 adapter 做 SPI 私有 adapter**，不进 `DataFetcherManager`、不实现通用板块能力；`base.py` 保持零改动 |
| **H-2** | `spi_time` 语义错误 | Java 是前一**自然日**15:00（非交易日）；DSA 已有 `get_effective_trading_date()`（`trading_calendar.py:196`）按交易所日历 | **用现成 `get_effective_trading_date` 修正为交易日语义**（有意修正，非复刻 Java）。→ §11.3 / §9 `spi_time.py` 改为薄封装调用 `get_effective_trading_date`；§13 加 D9 |
| **H-3** | `capital_flow_context` 字段假设错误 | 实际输出 `main_net_inflow/inflow_5d/inflow_10d`，且是实时块非历史序列，不能回算历史（`fundamental_adapter.py:416`） | **比价资金流代理改为：CMF(基于K线,历史可得) 为主 + `main_net_inflow` 等实际字段名(仅当日/近端,不回算历史)**。→ §24.3 / §23 字段名修正；历史比价仅用 CMF + RS，资金流代理仅用于当日增强 |
| **H-4** | `findMostLimit` 排名语义偏差 | Java 忽略传入 date、只取 latest batch（`PlateElementDayValueServiceI.java:27/34`） | **标注为有意修正**：DSA 按-anchor-日期查排行更正确。→ §13 加 D10 备注，非 bug |
| **H-5** | 两级并发失控 | `AnalysisTaskQueue` 默认 3 worker + 任务内 ThreadPool → 串行×并发失控（`task_queue.py:174/208`） | **单一并发层**：回算任务内部用 ThreadPool（板块级并发，上限可配），队列层串行提交。→ §13 加 D11；并发上限 = `min(32, cpu*4)` 实测调整 |

## 29.3 Medium 问题处置（一并采纳）

| # | 问题 | 修订 |
|---|---|---|
| **M-1** 交易日历 | 已由 H-2 解决（用 `get_effective_trading_date`） |
| **M-2** 停牌/退市/新股降级 | §11.1 / §13 加 coverage 阈值：行业指数可用 K 线 < 233 根时 SPI 标记低置信度，而非继续把它当完整样本 |
| **M-3** 任务状态真源 | `AnalysisTaskQueue` 为状态真源；`SpiBackfillTaskRun` 仅记回算业务元数据(起止日/板块数)，进程重启由队列恢复 |
| **M-4** 信号表幂等 | `SpiRotationSignal` 加 `UNIQUE(board_id,stock_code,trade_date,action)` 约束（§15 表定义补） |
| **M-5** CMF Σvolume=0 保护 | §24.1 补除零保护 + K线复权一致性(统一前复权) |
| **M-6** 路由聚合/建表范式 | endpoint 在 `api/v1/router.py` 注册；建表用 `Base.metadata.create_all()`（无 alembic，§9 "alembic" 字样修正） |
| **M-7** 文档自相矛盾 | `PlateSpiSnapshot` 不塞 `constituents_json`；成分股快照独立表（§15 已是独立表，§14 C3 撤销） |

---

## 29.4 修订后 §9 目录增量变更（相对原 §9）

```
变更：
- data_provider/xuangubao_fetcher.py  → 改为 src/services/spi/akshare_sw_adapter.py（SPI 私有 adapter，不进全局 fetcher 链）
                                         ※ 不再继承 BaseFetcher，不再改 data_provider/base.py
- cal_board_spi / 成分股均值路径       → 改为 cal_index_spi / 行业指数 direct SPI
- src/utils/spi_time.py               → 改为 src/services/spi/spi_time.py，薄封装 `get_effective_trading_date` + trading-date 迭代
- scheduler.py 盘后任务               → 改为 main.py `scheduled_task()` 链式追加 SPI 刷新
- alembic/  字样                      → 删除，建表统一用 storage.py 的 Base.metadata.create_all()
- 新增任务 1.0a                        → EMA 黄金样例对表（Java 5/13/233 逐值 vs Python 实现）
```

## 29.5 修订后置信度

| 维度 | 审查前 | 审查+修订后 |
|---|---|---|
| 架构对齐 | High | **High**（H-1 私有 adapter 后真零侵入）|
| 算法保真 | Medium（EMA seed 未定）| **High**（C-3 黄金样例对表 + C-1 Java 口径复刻）|
| 数据可行 | Low（153万请求爆炸）| **High**（C-2 改为行业指数 direct SPI，移除逐股请求爆炸）|
| 并发/恢复 | Medium（两级并发）| **High**（H-5 单一并发层 + M-3 状态真源）|
| **总体可执行性** | **Medium** | **High**（Codex/Gemini 共识：解决 C-1/C-2 后升至 High，现已解决）|

**结论**：方案经双模型审查 + 用户决策修订后，所有 Critical/High 已闭环，可进入阶段 1 编码。
**唯一编码前硬门槛**：任务 1.0（`akshare` 行业指数契约 smoke）+ 任务 1.0a（EMA 黄金样例对表）。
