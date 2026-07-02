# SPI 板块轮动指标迁移（Phase 1）— Implementation Tasks

> Change ID: spi-rotation-pricing-impl
> Spec: `docs/product-specs/spi-rotation-pricing-requirement-boundary.md`
> currentPhase: `1`
> 范围：**仅 Phase 1（SPI v1 迁移）**

---

## 拆分依据

> Parallel ID 语义：**同一 Parallel ID 内的任务彼此并行**；不同 Parallel ID / 无 Parallel ID 的单任务按 `depends-on` 串行。仅对"真有并行"的组赋 Parallel ID，单任务不单列 ID。

- `1.1` 与 `1.2` 是风险门槛任务，彼此并行（p1a）；但只分别阻塞 `1.3` 与 `1.5`，**不阻塞**同组无上游依赖的 `1.4`、`1.7`。
- `1.3 / 1.4 / 1.5 / 1.7` 四块无相互依赖，可并行（p1b）；其中 1.3←1.1、1.5←1.2 为组内任务的各自外部依赖。
- `1.6`（repo）单任务，仅依赖 `1.4`，串行。
- `1.8` 是 Phase 1 唯一汇聚点，依赖 adapter/calculator/repo/time 四块，串行。
- `1.9` 与 `1.12` 无相互依赖，可并行（p1c）；测试仅覆盖 adapter/calculator/service 离线测试。
- `1.10` 与 `1.11` 无相互依赖，可并行（p1d）。
- `1.13` 远端验收单任务，依赖全部前置完成。

---

### Phase 1: 门槛层（可并行 p1a）

- **current_phase**: `1`
- **task_phase**: `1`
- **Parallel ID**: `p1`
- **depends-on**: 无

- [ ] **1.1** 远端选股宝 smoke：DSA 远端环境（经 SSH MCP）实测 4 接口（`plate/rank`、`plate_set?id=`、`market/kline`、`plate/index_history`），验证 URL、成功码 `20000`、字段映射（`close_px -> close`、`prod_code/symbol`、成分股字段）、是否需要鉴权头、限流/超时边界 — depends-on: 无
- [ ] **1.2** EMA 黄金样例对表：取 Java `TA-Lib` 侧 `5/13/233` 周期样例，Python 侧完成逐值对齐，明确最终 seed/lookback 方案；未对齐前不得实现 `spi_calculator` — depends-on: 无

---

### Phase 1: 编码基础层（可并行 p1b）

- **current_phase**: `1`
- **task_phase**: `1`
- **Parallel ID**: `p1`
- **depends-on**: `p1a`

- [ ] **1.3** `src/services/spi/xuangubao_adapter.py`：实现 SPI 私有 XGB adapter（不接入 `DataFetcherManager`，不改 `data_provider/base.py`），提供 `get_plate_rank` / `get_board_constituents` / `get_kline` / `get_plate_history` 四个能力，并完成代码归一化（`600000.SS -> 600000` 等） — depends-on: `1.1`
- [ ] **1.4** `src/storage.py`：新增 `PlateSpiSnapshot` 与 `SpiBackfillTaskRun` 表，沿用 `declarative_base + Base.metadata.create_all()` 范式，不引入 `alembic` — depends-on: 无
- [ ] **1.5** `src/services/spi/spi_calculator.py`：实现 `ema_for_spi` / `cal_stock_spi` / `cal_board_spi`，以 `1.2` 的黄金样例结论为准，保证 SPI v1 口径可复刻 — depends-on: `1.2`
- [ ] **1.7** `src/utils/spi_time.py`：封装 Phase 1 的交易日锚点解析，统一得到可复用的日线 `trade_date/anchor_date`，供 service / repo / task 复用（复用 `get_effective_trading_date`，D9） — depends-on: 无

---

### Phase 1: 数据访问层（串行）

- **current_phase**: `1`
- **task_phase**: `1`
- **Parallel ID**: ``
- **depends-on**: `1.4`

- [ ] **1.6** `src/repositories/plate_spi_repo.py`：实现 SPI 快照 upsert 幂等、按交易日查询 Top N（按 anchor 日期，D10）、查询 N 日时序、任务运行记录 CRUD；唯一键以 `(board_id, trade_date)` 为准 — depends-on: `1.4`

---

### Phase 1: 服务编排汇聚点（串行）

- **current_phase**: `1`
- **task_phase**: `1`
- **Parallel ID**: ``
- **depends-on**: `1.3, 1.5, 1.6, 1.7`

- [ ] **1.8** `src/services/spi/plate_spi_service.py`：完成单板块 SPI 计算、全量板块并行编排、日终刷新、历史补算主流程；整合 XGB adapter、EMA 计算、repo 与时间锚点（优先 `StockRepository.get_range` 本地读源 miss 才回源，D1修订；ThreadPool 单一并发层，D11；coverage 60% 降级，D12） — depends-on: `1.3, 1.5, 1.6, 1.7`

---

### Phase 1: 异步与测试（可并行 p1c）

- **current_phase**: `1`
- **task_phase**: `1`
- **Parallel ID**: `p2`
- **depends-on**: `1.8`

- [ ] **1.9** 异步任务接入：将 `backfill_history` / `refresh_daily` 接入 `AnalysisTaskQueue`（D6），明确任务状态真源、进度更新与失败语义（M-3），任务内 ThreadPool 上限 `min(32,cpu*4)`，避免同步 HTTP 长任务 — depends-on: `1.8`
- [ ] **1.12** 测试：补齐 `test_xuangubao_adapter.py`、`test_spi_calculator.py`、`test_plate_spi_service.py`，覆盖字段映射、EMA/SPI 算法、并行编排与 service 级状态流转 — depends-on: `1.3, 1.5, 1.8`

---

### Phase 1: 接口与调度（可并行 p1d）

- **current_phase**: `1`
- **task_phase**: `1`
- **Parallel ID**: `p2`
- **depends-on**: `1.8, 1.9`

- [ ] **1.10** API：新增 `api/v1/endpoints/plate_spi.py`，并完成 `api/v1/router.py`、`api/v1/endpoints/__init__.py` 注册，提供 `POST /refresh`、`POST /backfill`、`GET /status/{task_id}`、`GET /rankings`（Top 30×100 日） — depends-on: `1.8, 1.9`
- [ ] **1.11** runtime scheduler：在 `src/services/runtime_scheduler.py` 接入日终 SPI 刷新任务，确保 Web/API 运行态下由 runtime scheduler 持有与触发（M-6，非 scheduler.py） — depends-on: `1.9`

---

### Phase 1: 收尾验收（串行）

- **current_phase**: `1`
- **task_phase**: `1`
- **Parallel ID**: ``
- **depends-on**: `p1d, 1.12`

- [ ] **1.13** 远端验收：按仓库约束（CLAUDE.md §5）在远端完成源码更新、临时挂载容器语法/离线测试（`py_compile` + `pytest -m "not network"`）、`dsa-server` 重启、`curl` 验证 4 个 SPI 端点、检查 SPI 快照表与任务状态记录 — depends-on: `p1d, 1.12`

---

## 总计

- **Total Tasks**: `13`
- **Task Phase**: `1`
- **可并行组（Parallel ID）**: `4`（`p1a` 1.1∥1.2 / `p1b` 1.3∥1.4∥1.5∥1.7 / `p1c` 1.9∥1.12 / `p1d` 1.10∥1.11）
- **串行单任务**: `3`（1.6 / 1.8 / 1.13）
- **关键汇聚点**: `1.8`（plate_spi_service）
- **最终验收入口**: `1.13`（远端验收）
- **编码前硬门槛**: `1.1` + `1.2`（p1a），未通过不得进入 p1b 编码
