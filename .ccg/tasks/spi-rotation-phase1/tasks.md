# SPI 板块轮动系统 阶段1：SPI v1 原逻辑迁移 — Implementation Tasks

> Change ID: spi-rotation-phase1
> currentPhase: 1（full-collaborate Phase 3 详细规划产出）
> 权威依据：`docs/product-specs/spi-rotation-pricing-requirement-boundary.md` §12 WBS + §29 多模型审查修订（以 §29 为准）
> **数据源决策变更（实施期）**：调研证实选股宝无法精确获取申万一级（type=2 返回 170 个自定义子行业，无专属接口），改用 **akshare 申万一级行业指数**（31 个）直接算 SPI。算法从「成分股聚合」改为「行业指数 close 直接算」。详见下方各 task 实际实现描述。
> 验收环境：本地 `.venv`（Python 3.11.2），直接跑 `scripts/ci_gate.sh` + 离线 pytest + FastAPI 本地起服务 curl 端点
> 代码风格：精简高效、无冗余、非必要不写注释文档（CLAUDE.md 0.2）

---

### Phase 1: 编码前硬门槛（连通性 + 算法保真）

- **current_phase**: `3`
- **task_phase**: `1`
- **Parallel ID**: `p1`
- **depends-on**: 无

- [×] **1.1** 选股宝连通性探查（§29 C-4）：经 SSH MCP / 本地实测 4 接口（plate/rank、plate/plate_set?id=、market/kline、plate 历史）。确认字段映射 `close_px→close`、`prod_code→symbol`、symbol 格式 `600000.SS`/`000001.SZ`、成功码 `20000`、GET 无鉴权头。产出 `connectivity-report.md`。⚠️ **实施期废弃选股宝路径**：实测 plate/rank type=2 返回 170 个自定义子行业，非申万一级，SPI 改用 akshare（见 2.3）。本产出保留为选股宝协议参考 — depends-on: 无
- [×] **1.2** EMA 黄金样例对表（§29 C-3）：取 Java `Indicators.emaForSPI` 在 5/13/233 周期的输出做逐值对表。定 Python 实现 = `ewm(adjust=False)` + 手写 SMA warm-up 复刻 TA-Lib seed 语义。产出 `ema-golden-sample.md`（Java vs Python 逐值表，对齐才算迁移成功）— depends-on: 无

---

### Phase 2: 数据层（建表 + 算法 + adapter + repository）

- **current_phase**: `3`
- **task_phase**: `2`
- **Parallel ID**: `p2`
- **depends-on**: p1

- [×] **2.1** `storage.py` 加 `PlateSpiSnapshot`（board_id[Integer], board_name[String(32),nullable], trade_date, spi, confidence/coverage 字段 M-2, UNIQUE(board_id,trade_date) D5；**不含 constituents_json** M-7；board_id 存申万代码 int 如 801010）+ `SpiBackfillTaskRun`（仅回算业务元数据：起止日/板块数，状态真源为 AnalysisTaskQueue M-3）表。建表用 `Base.metadata.create_all()`（**无 alembic** M-6）。board_name 字段为后续 #16 追加（向后兼容 nullable）— depends-on: 无
- [×] **2.2** `src/services/spi/spi_calculator.py` 纯算法：采用 1.2 对齐后的 EMA 实现，实现 `ema_for_spi(close_series)`（手写 SMA-seed 递推）、`cal_stock_spi(last_close, ema_map)`→0~8、`cal_index_spi(close_series)`→0~8（数据不足→-1 哨兵 D4）。⚠️ **决策变更（T4b）**：原 `cal_board_spi`（成分股聚合）已删除，改为行业指数直接算的 `cal_index_spi`。同步 `tests/test_spi_calculator.py`（mock close 序列，断言 0~8 + -1 边界 + SMA-seed 对表）— depends-on: 1.2
- [×] **2.3** `src/services/spi/akshare_sw_adapter.py`（§29 H-1，**决策变更 T5b**）：**SPI 私有 adapter，不继承 `BaseFetcher`、不进 `DataFetcherManager`、不实现 `get_sector_rankings` 等公共能力**，由 `PlateSpiService` 直接持有。`data_provider/base.py` **零改动**。实现 `get_sw_first_levels()`（调 `ak.sw_index_first_info`，返回 31 个申万一级 [{board_id, board_name, stock_count}]，board_id 去 .SI）+ `get_index_kline(board_id, back_count)`（调 `ak.index_hist_sw`，返回行业指数 close 序列）。akshare 1.18.64 实测可用。同步 `tests/test_akshare_sw_adapter.py`（mock akshare）。⚠️ 原 `xuangubao_adapter.py` + `test_xuangubao_fetcher.py` 已删除（选股宝路径废弃）— depends-on: 无（akshare 为 DSA 既有依赖）
- [×] **2.4** `src/repositories/plate_spi_repo.py`：`PlateSpiRepository` — `upsert_snapshot`（(board_id,trade_date) 幂等 D5，-1 过滤，含可选 board_name 参数）、`find_top_boards`（**按 anchor 日期查询** D10，过滤 spi=-1，ORDER BY spi DESC LIMIT N）、`find_board_series`（每板块取最近 N 日时序）— depends-on: 2.1

---

### Phase 3: 业务编排层（service）

- **current_phase**: `3`
- **task_phase**: `3`
- **Parallel ID**: `p3`
- **depends-on**: p2

- [×] **3.1** `src/services/spi/spi_time.py`（§29 H-2/D9）：薄封装调用现有 `src/core/trading_calendar.py:get_effective_trading_date`（交易日语义，有意修正 Java 前一自然日不严谨处理）。`spi_time(now)`→anchor — depends-on: 无（独立小模块，归此 Phase）
- [×] **3.2** `src/services/spi/plate_spi_service.py`（**决策变更 T8b**）：`compute_board_spi` 用 `AkshareSwAdapter.get_index_kline` 取行业指数 close 序列 → `cal_index_spi` → 0~8。全量并行（`ThreadPoolExecutor` 单一并发层 D11，上限 `min(32,cpu*4)` C2；队列层串行 upsert D11）。**coverage 语义变更**：无成分股概念，coverage = K 线是否 ≥ 233（足量 normal / 不足 low），取代原 60% 成分股阈值（M-2）。refresh_all 落库含 board_name。⚠️ 原 K 线读本地 StockDaily + 成分股聚合 + 选股宝回源逻辑已全部移除（行业指数每次直接回源 akshare，不落 per-stock 本地表）。同步 `tests/test_plate_spi_service.py`（行业指数 SPI + 并行 + upsert 幂等 + 失败隔离 + -1 不落库）— depends-on: 2.2, 2.3, 2.4, 3.1

---

### Phase 4: 任务编排 + API 层

- **current_phase**: `3`
- **task_phase**: `4`
- **Parallel ID**: `p4`
- **depends-on**: p3

- [×] **4.1** 异步任务接入：`backfill_history(start_date, end_date)` 提交 `AnalysisTaskQueue.submit_background_task`（**任务状态真源** M-3，真实状态枚举 `PENDING/PROCESSING/...`）+ 进度更新 `update_progress`；`refresh_daily`（日终刷新单日全量板块）。规避 Java 同步超时陷阱（A3）— depends-on: 3.2
- [×] **4.2** `api/v1/endpoints/plate_spi.py`（在 `api/v1/router.py` 注册，prefix `/plate-spi`，M-6）：`POST /refresh`、`POST /backfill`（返回 task_id，HTTP 202）、`GET /status/{task_id}`、`GET /rankings?date=&top_n=30&days=100`（复用 AnalysisTaskQueue 状态查询；rankings 响应每条含 `board_id`/`board_name`/`spi`/`confidence`/`coverage`/`series`）— depends-on: 4.1
- [×] **4.3** SPI 日终刷新接入（**方案变更**：改 `main.py` 而非 `scheduler.py`）：调研发现 `Scheduler` 是单任务模型（`_task_callback` 单槽位被现有分析任务占用），且 `set_daily_task` 有 `runtime_scheduler` 依赖。决策方案 B：在 `main.py:1449` 的 `scheduled_task()` 内 `run_full_analysis` 之后追加 `SpiTaskRunner().refresh_daily()`，独立 try/except 异常隔离（不拖垮主分析），延迟 import 避免循环依赖。**`scheduler.py` 零改动** — depends-on: 4.1

---

### Phase 5: 本地验收

- **current_phase**: `3`
- **task_phase**: `5`
- **Parallel ID**: `p5`
- **depends-on**: p4

- [×] **5.1** 本地 `.venv` 验收（直接本机执行，不走远端）：
  1. **静态门**：`.venv/bin/python -m py_compile` 覆盖本次新增/改动文件（`src/storage.py`、`src/services/spi/*.py`、`src/repositories/plate_spi_repo.py`、`api/v1/endpoints/plate_spi.py`、`main.py`）+ `flake8 . --select=E9,F63,F7,F82` — ✅ clean
  2. **业务单测**：`.venv/bin/python -m pytest tests/test_spi_calculator.py tests/test_akshare_sw_adapter.py tests/test_plate_spi_service.py` — ✅ 39 passed（断言 0~8 + -1 哨兵 + upsert 幂等 + 并行 + 失败隔离）。全量 `pytest -m "not network"` 按需另行执行
  3. **建表落地**：本地 SQLite 触发 `Base.metadata.create_all()` 后，查 `PlateSpiSnapshot`（含 board_name）/`SpiBackfillTaskRun` 两表 schema 存在 + UNIQUE(board_id,trade_date) 约束生效 — ✅ 验证通过
  4. **API 联调**：TestClient 起本地 FastAPI → 4 端点：`POST /plate-spi/refresh`（202+task_id）、`POST /plate-spi/backfill`（202+task_id，倒序 400）、`GET /plate-spi/status/{task_id}`（200/404）、`GET /plate-spi/rankings?top_n=5`（200，返回含中文 board_name 的 Top N）— ✅ 全通
  5. **网络项**：akshare 申万接口本地可达，实测 `refresh_all()` 31 个申万一级全成功落库非 -1 快照（基础化工 spi=8 / 建筑材料 spi=7 / 电子 spi=6）— ✅ 实测通过 — depends-on: 4.2, 4.3

---

## 总计

**Total Tasks**: 12（1.1–1.2 / 2.1–2.4 / 3.1–3.2 / 4.1–4.3 / 5.1）+ 实施期改造 4 项（T4b/T5b/T8b/#16）
**依赖链**: p1(并行) → p2(并行) → p3 → p4(4.1先行,4.2/4.3并行) → p5(本地`.venv`验收)
**零侵入约束**: 不触碰 `get_sector_rankings`/`get_belong_boards`/`capital_flow_context`；`data_provider/base.py` 不改动（H-1，akshare adapter 独立 import，不复用公共能力）；`scheduler.py` 不改动（方案 B）
**实施期关键变更**（vs 原规划，以实际实现为准）：
- **数据源**：选股宝 plate/rank（591 板块混杂）→ akshare 申万一级行业指数（31 个，权威）。选股宝 adapter 及其测试已删除
- **算法**：成分股 SPI 聚合（`cal_board_spi`）→ 行业指数 close 直接算（`cal_index_spi`），不再依赖成分股 API
- **coverage**：成分股有效率 60% 阈值 → K 线是否 ≥ 233 根（足量 normal / 不足 low）
- **K 线读源**：本地 StockDaily + 选股宝回源落盘 → 行业指数每次直接回源 akshare（无 per-stock 本地落盘）
- **board_name**：PlateSpiSnapshot 追加 String(32) nullable 字段，全链路入库（#16）
- **日终任务接入**：`scheduler.py` set_daily_task → `main.py` scheduled_task 链式追加（方案 B，scheduler 单任务模型约束）
- **目录修正**（vs 原 §12）：`xuangubao_fetcher.py`→`akshare_sw_adapter.py`（原 `xuangubao_adapter.py` 已删）；新增 1.2 EMA 黄金样例；`spi_time` 用 `get_effective_trading_date`（D9）
