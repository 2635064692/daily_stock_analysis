# SPI 板块轮动系统 阶段2：SPI v2 + 轮动闭环（v2 因子 + 成分股快照 + 轮动信号） — Implementation Tasks

> Change ID: spi-rotation-phase2
> currentPhase: 1-planning
> 权威依据：`docs/product-specs/spi-rotation-pricing-requirement-boundary.md` §15–§20 + §29 审查决策
> **前置依赖**：阶段1全部产出已落地（行业指数适配层 / SPI 快照表 / 异步任务 / API 4 端点）
> 验收环境：本地 `.venv`（Python 3.11.2），离线 pytest + FastAPI TestClient

---

### Phase 1: 编码前硬门槛（成分股映射审计 + v2 因子数学基线）

- **current_phase**: `1-planning`
- **task_phase**: `1`
- **Parallel ID**: `p1`
- **depends-on**: 无

- [×] **1.1** 成分股链路映射审计（对应 §19.1 R-1 风险）：枚举 AlphaSift 现有成分股 API（`get_belong_boards` / 概念题材接口），实测以申万一级行业名（如"基础化工"）为 key 能否稳定获取成分股列表；统计对齐率（31 个行业中有多少可稳定拿到 ≥5 只成分股）。产出 `constituents-audit.md`，决定 2.4 实施方案（直接用 / 建名称映射表 / 降级单龙头兜底）— depends-on: 无
- [×] **1.2** EMA 序列化接口变更影响分析：枚举 `ema_for_spi` 当前所有调用方（`cal_index_spi` / `plate_spi_service.py` / tests），评估升级为返回完整序列对 phase1 调用链的 BC 兼容性。设计兼容方案（新函数 `ema_for_spi_v2` vs 原地升级 + 更新调用方）。产出兼容策略决策（不超过 2 行）— depends-on: 无

---

### Phase 2: 数据层扩展（schema + 因子算法 + 打分引擎）

- **current_phase**: `1-planning`
- **task_phase**: `2`
- **Parallel ID**: `p1`
- **depends-on**: 无

> **注**：Phase 2 与 Phase 1 同属 Parallel ID `p1`，但 Phase 2 内部各任务无跨文件依赖，可在 Phase 1 完成后立即并行开始。为清晰起见拆分为两个 Phase 块，实际执行时 2.1/2.2/2.3（schema + 纯算法）可在审计结果出来前启动。

- [×] **2.1** `storage.py` schema 扩展（D2.1 ALTER）：`PlateSpiSnapshot` 新增可空列 `v2_score FLOAT nullable`（保证向后兼容，无 alembic）；新建 `SpiRotationSignal` 表（board_id, stock_code, trade_date, action=BUY/SELL, reason, UNIQUE(board_id,stock_code,trade_date,action) — §29 M-4）；新建 `ConstituentSnapshot` 表（board_id, trade_date, stock_codes_json TEXT, UNIQUE(board_id,trade_date)）。建表/加列用 `Base.metadata.create_all()` + `try: op.add_column` 模式（避免"already exists"报错）— depends-on: 无
- [×] **2.2** `src/services/spi/spi_factors/` 4 因子模块（S17 可插拔）：
  - `base.py`：`SpiFactor` 抽象基类 `name/weight/compute(ema_series: dict[int,list], last_close: float) -> float`
  - `ema_direction.py`：F1，各周期 ema[-1] vs ema[-lookback=5]，上升周期数/8 → [0,1]
  - `alignment.py`：F2，5>13>21>34>55>89>144>233 满足对数/7 → [0,1]
  - `separation.py`：F3，(short_avg-long_avg)/long_avg 归一化 → [0,1]，short=[5,13,21]，long=[89,144,233]
  - `compression.py`：F4，std(ema_now) vs std(ema_prev=5期前)，扩张为正/压缩为负 → [-1,1]
  - `__init__.py`：REGISTRY 默认权重 {F1:0.3, F2:0.3, F3:0.25, F4:0.15}（C2.1 默认加权和，配置化可调）
  同步 `tests/test_spi_factors.py`，必须覆盖：
  - 各因子正常路径：输出在约定范围内（F1/F2/F3 → [0,1]，F4 → [-1,1]）
  - F1 边界：ema 序列长度恰好等于 lookback（5）vs 不足 5 时的处理（不崩溃）
  - F3 边界：long_avg == 0 时除零保护（返回 0.0 或约定值，不抛异常）
  - F4 边界：ema 序列长度 < lookback+1 时的降级行为（返回 0.0 或约定值）
  - 全量 REGISTRY：8 周期均存在时，所有因子均可 compute 且不报错
  — depends-on: 无
- [×] **2.3** `src/services/spi/spi_scorer.py` 打分引擎：`SpiScorer.score(ema_series, last_close) -> float`；遍历 REGISTRY，调用各因子 compute；加权和组合（默认）；归一化到 [0,100]（线性拉伸，处理 F4 [-1,1] 的负值区间）。同步 `tests/test_spi_scorer.py`，必须覆盖：
  - 正常路径：输出 ∈ [0,100]，权重变更后分数相应变化
  - 全因子最大值 → score == 100（不超出上界）
  - 全因子最小值（含 F4=-1）→ score == 0（不产生负值）
  - 自定义权重（非默认 REGISTRY）时，归一化仍有效
  — depends-on: 2.2

---

### Phase 3: EMA 序列化升级 + v2 快照落库

- **current_phase**: `1-planning`
- **task_phase**: `3`
- **Parallel ID**: `p2`
- **depends-on**: p1

- [×] **3.1** `spi_calculator.py` 升级（基于 1.2 兼容方案）：新增 `ema_for_spi_v2(close_series) -> dict[int, list]` 返回完整 EMA 序列（每个 period 对应一条完整 list，长度 = len(close) - period + 1，不足则跳过）；新增 `cal_stock_spi_v2(close_series) -> float` 内部调用 `ema_for_spi_v2` + SpiScorer，返回 [0,100]；原 `ema_for_spi` / `cal_index_spi` **不删除、不改签名**（phase1 测试/API 继续调用）。同步 `tests/test_spi_calculator.py` 追加 v2 路径用例：
  - `ema_for_spi_v2` 返回 dict[int, list]，list 非空且值为浮点
  - 数据不足某周期时该 period 不出现在结果 dict（与 v1 行为一致）
  - `cal_stock_spi_v2(250根上升序列)` → score ∈ [0,100]
  - **BC 回归**：原 `TestEmaForSpi` / `TestCalIndexSpi` 全部仍通过（不因升级而回归）
  — depends-on: 1.2, 2.3
- [×] **3.2** `plate_spi_service.py` 扩展 v2 计算：`compute_board_spi_v2(board_id, trade_date)` — 复用 `AkshareSwAdapter.get_index_kline` 取指数 K 线 → `ema_for_spi_v2` → `SpiScorer.score` → 落库 `v2_score` 列（upsert 扩展，不新建行）；`refresh_all_v2` 并行版本（复用 phase1 ThreadPoolExecutor 单一并发层 D11）— depends-on: 3.1, 2.1
- [×] **3.3** `plate_spi_repo.py` 扩展 v2 读写：`upsert_v2_score(board_id, trade_date, v2_score)` / `find_top_boards_v2(date, top_n)` 按 v2_score 排序 / `upsert_rotation_signal` / `find_rotation_signals(date, board_id)`— depends-on: 2.1

---

### Phase 4: 成分股快照 + 轮动信号引擎

- **current_phase**: `1-planning`
- **task_phase**: `4`
- **Parallel ID**: `p3`
- **depends-on**: p2

- [×] **4.1** `src/utils/constituents_snapshot.py` point-in-time 成分股锁定（S2）：基于 1.1 审计结论选择实现方案（直接调成分股接口 / 名称映射表）；`save_constituents(board_id, trade_date, stock_codes: list[str])` 写 `ConstituentSnapshot`；`get_constituents(board_id, trade_date) -> list[str]` 按日查询历史名单；日终任务首次启用后开始积累（旧历史不承诺回补真实名单，§19.1 D2.4）。同步 `tests/test_constituents_snapshot.py`，必须覆盖：
  - `save_constituents` 幂等：相同 (board_id, trade_date) 重复写不产生重复行（UNIQUE 约束验证）
  - `get_constituents` 返回对应日期名单，不返回其他日期数据
  - 查无此日 → 返回空列表，不抛异常
  — depends-on: 1.1, 2.1
- [×] **4.2** `src/services/spi/rotation_service.py` 轮动闭环（仅观察/提示，不自动交易 D2.5）：
  - `update_watchpool(trade_date, top_n=30)` — 从 `find_top_boards_v2` 取 Top N 板块维护观察池（内存+持久化）
  - `check_entry(board_id, trade_date, constituents)` — 板块内个股回踩 20日EMA+量比>1.2（C2.2 默认值，来自 rotation_entry.yaml）
  - `check_exit(board_id, trade_date, top_m=50)` — 板块排名跌出 Top M 或个股跌破 N 日 EMA
  - 触发信号写 `SpiRotationSignal`（幂等 UNIQUE 约束 M-4）
  同步 `tests/test_rotation_service.py`，必须覆盖：
  - 入场信号正常触发：回踩均线 + 量比满足阈值 → 写入 BUY 信号
  - 出场信号正常触发：板块排名跌出 Top M → 写入 SELL 信号
  - 信号幂等：相同 (board_id, stock_code, trade_date, action) 重复触发 → 不重复写入（UNIQUE 约束）
  - 观察池移除：板块 v2 排名跌出 Top N → 从观察池移除，不产生 BUY 信号
  - 降级路径：`get_constituents` 返回空列表时，跳过入场/出场检查，不报错
  — depends-on: 3.2, 3.3, 4.1
- [×] **4.3** `strategies/rotation_entry.yaml` 策略配置：回踩均线参数（entry_ema_period=20, volume_ratio_threshold=1.2）+ 出场参数（exit_top_m=50, exit_ema_period=5）；格式对齐现有 `strategies/*.yaml` 范式— depends-on: 4.2

---

### Phase 5: API 扩展 + 日终任务接入

- **current_phase**: `1-planning`
- **task_phase**: `5`
- **Parallel ID**: `p4`
- **depends-on**: p3

- [×] **5.1** `api/v1/endpoints/plate_spi.py` 新增端点（改现有文件）：
  - `GET /v2/rankings?date=&top_n=30&days=100` — 按 v2_score 排名，**可先独立上线**（§19.1 D2.7）
  - `GET /rotation/signals?date=&board_id=` — 查询轮动信号（随 4.2 一起验收）
  — depends-on: 3.3, 4.2
- [×] **5.2** `spi_task_runner.py` 日终任务扩展：`refresh_daily` 中在 v1 快照后追加 `refresh_all_v2`（v2 计算）+ 轮动信号生成（`rotation_service.update_watchpool` + 信号写入）；各环节独立 try/except 异常隔离（不拖垮主流程）。追加 `tests/test_spi_task_runner.py` 用例：
  - v2 计算抛异常时，v1 快照落库结果不受影响（异常隔离验证）
  - 成分股快照未落盘（`get_constituents` 返回空）时，轮动信号生成被跳过，`refresh_daily` 仍正常返回
  — depends-on: 3.2, 4.2

---

### Phase 6: 验收

- **current_phase**: `1-planning`
- **task_phase**: `6`
- **Parallel ID**: `p5`
- **depends-on**: p4

- [×] **6.1** 本地验收：
  1. **静态门**：`py_compile` 覆盖本次所有新增/改动文件 + `flake8 . --select=E9,F63,F7,F82`
  2. **业务单测**：`pytest tests/test_spi_factors.py tests/test_spi_scorer.py tests/test_spi_calculator.py tests/test_constituents_snapshot.py tests/test_rotation_service.py tests/test_spi_task_runner.py`（含 v2 路径 + 5.2 扩展用例）
  3. **建表落地 smoke**：SQLite 触发 `Base.metadata.create_all()` 后，验证 `PlateSpiSnapshot.v2_score` 列 / `SpiRotationSignal` 表（含 UNIQUE 约束）/ `ConstituentSnapshot` 表均存在
  4. **API 联调**：TestClient `GET /plate-spi/v2/rankings` 返回 v2_score ∈ [0,100]；`GET /plate-spi/rotation/signals` 200 响应
  5. **网络项**（可选）：实测 `refresh_all_v2()` 31 个申万一级 v2_score 落库— depends-on: 5.1, 5.2

---

## 总计

**Total Tasks**: 17（1.1–1.2, 2.1–2.3, 3.1–3.3, 4.1–4.3, 5.1–5.2, 6.1）
**测试文件**：新建 5（test_spi_factors / test_spi_scorer / test_constituents_snapshot / test_rotation_service）+ 追加 2（test_spi_calculator v2 路径 / test_spi_task_runner 扩展用例）
**新建生产文件**：11（spi_factors/×5 + spi_scorer.py + rotation_service.py + constituents_snapshot.py + rotation_entry.yaml + 共 3 个测试文件视上方计算）
**改动文件**：6（storage.py + spi_calculator.py + plate_spi_service.py + plate_spi_repo.py + plate_spi.py + spi_task_runner.py）
**零侵入**：`get_sector_rankings` / `get_belong_boards` / `capital_flow_context` / `data_provider/base.py`
