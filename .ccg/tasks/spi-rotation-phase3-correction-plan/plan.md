# SPI 阶段3修正方案：从动量追强切换到缠论 S/P 比价

> 适用范围：仅定义 phase3 的**修正实现方案**，不直接改动生产代码。  
> 输入依据：
> - 现有 phase3 需求与实现（`RS + CMF + Flow`）
> - `~/.claude/plans/atomic-tumbling-shore.md` 初稿
> - 对当前仓库真实数据路径 / schema 行为 / 测试面的可行性审查

---

## 1. 背景与目标

当前 phase3 的主排序逻辑是：

- `RS(20)`：20日收益板块内百分位
- `CMF(20)`：量价资金积聚信号
- `Flow`：`capital_flow_context` 提供的主力净流入代理

其本质是**动量追强**，即“谁更强、谁更有资金流入，谁排前面”。

修正后的目标是：

- 把 phase3 改成**缠论式 S/P 比价系统**：优先找“市值占比低于其行业地位占比”的低估个股；
- 保留 `CMF/Flow` 作为“补涨开始启动”的确认因子；
- 保留 `rs_score` 作为诊断字段，但不再进入 `total` 主分数；
- 对当前仓库保持尽量小的侵入，不重写 phase2 轮动逻辑。

一句话：

> `total` 从“当前谁最强”改为“谁更低估且开始被资金确认”。

---

## 2. 对 atomic-tumbling-shore 初稿的必要修正

初稿方向是对的，但存在 6 个必须修正点：

### 2.1 `get_fundamental_context` 路径修正

初稿把净利润读取路径写成：

```python
fctx["earnings"]["financial_report"]["net_profit_parent"]
```

当前仓库真实结构是：

```python
fctx["earnings"]["data"]["financial_report"]["net_profit_parent"]
```

因此实现中必须统一写成：

```python
financial_report = (((fctx.get("earnings") or {}).get("data") or {}).get("financial_report") or {})
profit = financial_report.get("net_profit_parent")
report_date = financial_report.get("report_date")
```

### 2.2 `create_all()` 不会自动补列

当前仓库只有“新表创建”依赖 `Base.metadata.create_all()`；存量 SQLite 表补列依赖显式 `_ensure_*_columns()`。

因此若 `PricingSnapshot` 新增 `sp_ratio` / `sp_score`，则必须在 `src/storage.py` 新增类似：

```python
def _ensure_pricing_snapshot_columns(self) -> None:
    ...
```

并在 `DatabaseManager.__init__()` 中挂入执行链。

> 不能只写“存量库手工 ALTER TABLE”，否则方案不可落地到当前 DSA 的默认运行路径。

### 2.3 分母口径必须基于同一 eligible 集合

初稿把：

- `board_total_mktcap = Σ(所有有效 total_mv)`
- `board_total_profit = Σ(所有 >0 的净利润)`

分开求和，这会导致 S 分母和 P 分母来自不同样本集合，数学上不一致。

修正规则：

- `eligible_stock = total_mv > 0 and net_profit_parent > 0`
- 只有 `eligible_stock` 才进入板块分母：
  - `board_total_mktcap = Σ(total_mv of eligible)`
  - `board_total_profit = Σ(net_profit_parent of eligible)`
- 不满足条件的个股：`sp_ratio = None`，`status = missing_core_factor`

### 2.4 报告期一致性必须显式定义

当前 `financial_report` 中存在 `report_date`，但 phase3 现有实现未涉及财报口径。

修正方案定义：

- **P0 可行版本**：允许使用“各股票最新已披露”的 `net_profit_parent`，但必须：
  - 读取并保留 `report_date`
  - 在 run 级别记录“是否存在混合报告期”
- **P1 严格版本**：按板块统一报告期筛选，只让同一 `report_date` 的成分股参与 S/P 计算

为兼顾最小侵入与当前可实施性，本方案采用：

- **phase3 修正 P0：先允许混合报告期，但将其作为批次诊断信息明确记录**。

推荐在 `PricingFactorRun.error` 或新增 metadata 字段中标记：

- `mixed_profit_report_date`
- `dominant_report_date=<YYYY-MM-DD>`

> 若不记录该信息，后续无法解释同板块不同股票的 P 值口径差异。

### 2.5 API 需暴露 `sp_ratio/sp_score`

如果 `total` 改成 SP 主导，但 API 仍只返回 `rs_score/cmf/flow_score`，则线上排障不可观测。

因此 `plate_pricing.py` 必须最小补充：

- `sp_ratio`
- `sp_score`

`rs_score` 保留返回，不改含义。

### 2.6 测试不能只改 service

初稿测试面过窄。修正后至少要覆盖：

- SP 因子纯算法
- service 主路径
- repo 新字段透传 / 排序 / upsert
- API 返回新字段

否则 schema / repo / API 三层无闭环。

---

## 3. 修正后的范围与文件矩阵

## 3.1 必改文件（9个）

| 文件 | 动作 | 说明 |
|---|---|---|
| `src/services/pricing/sp_ratio.py` | 新建 | S/P 纯算法 |
| `src/storage.py` | 修改 | `PricingSnapshot` 增列 + SQLite backfill |
| `src/repositories/pricing_repo.py` | 修改 | 透传 `sp_ratio/sp_score` |
| `src/services/pricing_service.py` | 修改 | 主因子从 RS 改为 SP |
| `api/v1/endpoints/plate_pricing.py` | 修改 | 返回 `sp_ratio/sp_score` 诊断字段 |
| `tests/test_pricing_sp_ratio.py` | 新建 | SP 因子单测 |
| `tests/test_pricing_service.py` | 修改 | service 场景更新 |
| `tests/test_pricing_repo.py` | 修改/扩展 | repo 新字段与 upsert 语义 |
| `tests/test_plate_pricing_api.py` | 修改/扩展 | API 返回新字段 |

## 3.2 不改文件

- `src/services/pricing/cmf.py`
- `src/services/pricing/capital_proxy.py`
- `src/services/pricing/relative_strength.py`
- `src/services/spi/spi_task_runner.py`

它们仍可复用，只是角色从主因子改为辅助 / 诊断。

---

## 4. 数据契约

## 4.1 新增字段

`src/storage.py -> PricingSnapshot` 追加：

```python
sp_ratio = Column(Float)   # 原始 S/P，<1 低估
sp_score = Column(Float)   # 1/(1+sp_ratio)，高分=更低估
```

`rs_score` 保持不动：

- 继续存放 20 日动量百分位
- 仅用于诊断 / 后续混合模式，不进入 phase3 修正后的主总分

## 4.2 SQLite 存量库补列

在 `src/storage.py` 增加：

```python
def _ensure_pricing_snapshot_columns(self) -> None:
    if not self._is_sqlite_engine:
        return
    existing = {column["name"] for column in inspect(self._engine).get_columns(PricingSnapshot.__tablename__)}
    for name in ("sp_ratio", "sp_score"):
        if name not in existing:
            ALTER TABLE pricing_snapshot ADD COLUMN ...
```

并挂到 `DatabaseManager.__init__()`：

```python
Base.metadata.create_all(self._engine)
self._ensure_llm_usage_telemetry_columns()
self._ensure_spi_v2_columns()
self._ensure_pricing_snapshot_columns()
...
```

> 若不补这一层，方案只对新建数据库成立，不符合当前项目默认运行方式。

---

## 5. 因子定义

## 5.1 S/P 原始比值

新建 `src/services/pricing/sp_ratio.py`：

```python
def calc_sp_ratio(stock_mktcap, board_total_mktcap, stock_profit, board_total_profit):
    """
    S = stock_mktcap / board_total_mktcap
    P = stock_profit / board_total_profit
    SP = S / P
    返回 None 条件：
    - 任一参数 None
    - board_total_mktcap <= 0
    - board_total_profit <= 0
    - stock_mktcap <= 0
    - stock_profit <= 0
    """
```

## 5.2 S/P 分数映射

```python
def sp_ratio_to_score(sp_ratio):
    """score = 1 / (1 + sp_ratio)"""
```

语义：

- `sp_ratio < 1` → `sp_score > 0.5`（低估）
- `sp_ratio = 1` → `sp_score = 0.5`（公允）
- `sp_ratio > 1` → `sp_score < 0.5`（高估）

---

## 6. pricing_service 修正设计

## 6.1 数据读取路径

在单一成分股循环中收集：

1. `bars = StockRepository.get_range(...)` → `CMF` + `RS` 诊断
2. `quote = manager.get_realtime_quote(code)` → `total_mv`
3. `fctx = manager.get_fundamental_context(code)` → `earnings.data.financial_report.net_profit_parent`
4. `flow = manager.get_capital_flow_context(code)` → `main_net_inflow` 代理

推荐形态：

```python
raw_rs: List[Optional[float]] = []
raw_cmf: List[Optional[float]] = []
raw_flow: List[Optional[float]] = []
raw_mktcap: List[Optional[float]] = []
raw_profit: List[Optional[float]] = []
raw_profit_report_date: List[Optional[str]] = []
```

## 6.2 运行时成本策略

注意：`get_fundamental_context()` 内部本身会触发一次 realtime quote。

### P0 方案（本次采用）
- 直接使用现有对外方法：`get_realtime_quote()` + `get_fundamental_context()`
- 接受内部存在一次重复 quote 的成本
- 维持实现简单，不深入重构数据提供层

### P1 优化（暂不进入本次实施）
- 为 `pricing_service` 提供轻量 fundamental bundle 直取路径，绕开重复 valuation 获取

> 本修正方案以**可落地**优先，不在本次改 pricing data provider 内部契约。

## 6.3 eligible 集合与板块分母

定义：

```python
eligible = (
    total_mv is not None and total_mv > 0 and
    profit is not None and profit > 0
)
```

然后：

```python
board_total_mktcap = sum(total_mv of eligible)
board_total_profit = sum(profit of eligible)
```

逐股：

- 若当前 stock 非 eligible → `sp_ratio=None`
- 若板块总分母任一 <=0 → 全板块 `sp_ratio=None`

## 6.4 报告期规则

本次采用 **P0 诊断优先规则**：

- 每股读取 `financial_report.report_date`
- 不强制整板统一报告期
- 但必须在 batch 级别统计：
  - `distinct_profit_report_dates`
  - `dominant_profit_report_date`
  - 是否 `mixed_profit_report_date`

由于当前 `PricingFactorRun` 没有 metadata JSON 字段，P0 可先：

- 在 `error` 为空时不塞混合报告期信息；
- 在日志中输出；
- 如需长期诊断，再在后续版本给 `PricingFactorRun` 增 metadata 字段。

> 若未来要提升缠论一致性，可升级到“统一报告期筛选”的 P1 版本。

---

## 7. 总分合成

## 7.1 权重

```python
_BASE_W_SP = 0.6
_BASE_W_CMF = 0.3
_BASE_W_FLOW = 0.1

_BASE_W_SP_NO_FLOW = 0.667
_BASE_W_CMF_NO_FLOW = 0.333
```

## 7.2 status / total 规则

| SP | CMF | Flow | 批次状态 | 单股 status | total |
|---|---|---|---|---|---|
| None | any | any | partial/failed | `missing_core_factor` | None |
| ok | None | any | partial/failed | `missing_core_factor` | None |
| ok | ok | ok (`flow_enabled`) | ok/partial | `ok` | `0.6*sp + 0.3*cmf + 0.1*flow` |
| ok | ok | None (`flow_enabled`) | partial | `degraded` | `0.667*sp + 0.333*cmf` |
| ok | ok | — (`flow_disabled`) | ok/partial | `ok` | `0.667*sp + 0.333*cmf` |

修正点：

- `rs_score` 不再是核心因子，不参与 total
- `cmf_score` 仍用 `(cmf + 1) / 2`
- `flow_score` 仍用板块内百分位

## 7.3 factor_mask 修正

原先是 `rs,cmf,flow`。修正后应改成：

- `sp,cmf,flow`
- `sp,cmf`
- 缺核心因子则 `None`

---

## 8. repo / API 修正

## 8.1 pricing_repo.py

需要透传：

- `sp_ratio`
- `sp_score`

改动点：

- `upsert_pricing()`
- `_upsert_pricing_in_session()`
- `save_pricing_batch()` snapshots 透传

## 8.2 plate_pricing.py

API 返回项追加：

```python
{
    "stock_code": r.stock_code,
    "total": r.total,
    "rs_score": r.rs_score,
    "sp_ratio": r.sp_ratio,
    "sp_score": r.sp_score,
    "cmf": r.cmf,
    "flow_score": r.flow_score,
    "status": r.status,
    "factor_mask": r.factor_mask,
}
```

这样保留原动量诊断，又把新的比价因子暴露出来。

---

## 9. 测试闭环

## 9.1 新增

### `tests/test_pricing_sp_ratio.py`
覆盖：

- `SP < 1` → `score > 0.5`
- `SP = 1` → `score = 0.5`
- `SP > 1` → `score < 0.5`
- 任一参数 None → None
- `stock_profit <= 0` → None
- `board_total_profit <= 0` → None
- `board_total_mktcap <= 0` → None

## 9.2 修改

### `tests/test_pricing_service.py`
补充/调整：

- `SP None → missing_core_factor`
- `SP + CMF 正常, flow_disabled → total 只由 SP/CMF 构成`
- `rs_score` 仍保留落库但不参与 total
- `factor_mask` 从 `rs,...` 切到 `sp,...`

### `tests/test_pricing_repo.py`
补充：

- `sp_ratio/sp_score` 成功落库
- same-day upsert 后新值覆盖旧值
- 排序仍按 `total` 走

### `tests/test_plate_pricing_api.py`
补充：

- 返回 `sp_ratio/sp_score`
- 显式 `trade_date` / latest trade date 默认路径不受影响

---

## 10. 实施顺序

### Layer 1：schema + 纯算法
1. `src/services/pricing/sp_ratio.py`
2. `src/storage.py` 新列 + `_ensure_pricing_snapshot_columns()`

### Layer 2：repo 透传
3. `src/repositories/pricing_repo.py`
4. `tests/test_pricing_repo.py`

### Layer 3：service 主逻辑
5. `src/services/pricing_service.py`
6. `tests/test_pricing_sp_ratio.py`
7. `tests/test_pricing_service.py`

### Layer 4：可观测性
8. `api/v1/endpoints/plate_pricing.py`
9. `tests/test_plate_pricing_api.py`

---

## 11. 验证

建议最小验证集：

```bash
python3 -m py_compile src/services/pricing/sp_ratio.py \
                     src/services/pricing_service.py \
                     src/storage.py \
                     src/repositories/pricing_repo.py \
                     api/v1/endpoints/plate_pricing.py

python3 -m pytest tests/test_pricing_sp_ratio.py \
                  tests/test_pricing_service.py \
                  tests/test_pricing_repo.py \
                  tests/test_plate_pricing_api.py -q
```

若环境允许，再补：

```bash
python3 -m pytest tests/test_spi_task_runner.py -q
```

---

## 12. 最终判断

该修正方案在当前仓库内**可实施**，前提是接受以下现实边界：

1. `P` 采用 `net_profit_parent`，不做营收 fallback；因此部分股票会被自然筛掉；
2. P0 先接受“最新已披露财报混合”的现实，但要把混合报告期作为诊断显式化；
3. 运行成本会高于当前动量版 phase3，但仍在可接受范围内；
4. 若不补 schema backfill / API 诊断字段 / repo/API 测试，本方案就不算完整落地。

一句话总结：

> 可行，但必须把“真实数据路径、SQLite 补列、同一 eligible 集合、报告期诊断、API 暴露、测试闭环”六个点一起修正，才能从概念方案变成可执行方案。
