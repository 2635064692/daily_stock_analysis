# SPI 阶段3修正方案：从动量追强切换到缠论 S/P 比价

> 适用范围：定义 phase3 从 `RS + CMF + Flow` 动量排序切换到缠论式 **S/P 比价** 的修正方案，不直接改动生产代码。  
> 关联文档：
> - `docs/product-specs/spi-rotation-pricing-requirement-boundary.md`
> - `~/.claude/plans/atomic-tumbling-shore.md`
>
> 目标：在保留当前 phase2 板块轮动闭环的前提下，把 phase3 的个股排序语义从“谁最强”改成“谁更低估且开始被资金确认”。

---

## 1. 用户问题与修正目标

当前 phase3 解决的是：

- 在已选定板块内，按 `RS(20)`、`CMF(20)`、`Flow` 给个股排序；
- 识别“当前更强、更有资金流入”的标的；
- 服务于 same-day 板块内相对强弱排名。

这套逻辑可工程落地，但与缠论“比价系统”的原始目标不同。

缠论比价系统更强调：

- 个股**市值占比**（S）
- 个股**行业地位占比**（P）
- 通过 `S/P` 找到“市值低于其应得份额”的低估品种
- 再等待比价复归、补涨与资金确认

因此修正目标为：

- 将 phase3 的主因子从 `RS` 改为 `SP`；
- 保留 `CMF/Flow` 作为“补涨启动”的确认层；
- 保留 `rs_score` 作为诊断字段，但不再主导 `total`；
- 让 phase2 继续负责“选强板块”，phase3 改为“强板块内找低估股”。

一句话：

> `total` 从“当前谁最强”改为“谁更低估且开始被资金确认”。

---

## 2. 对现有初稿的必要修正

对 `atomic-tumbling-shore` 初稿进行仓库可行性校验后，确认以下 6 个点必须修正，否则方案不可直接落地。

### 2.1 `get_fundamental_context` 的读取路径需要修正

初稿假设净利润读取路径是：

```python
fctx["earnings"]["financial_report"]["net_profit_parent"]
```

当前仓库真实结构是：

```python
fctx["earnings"]["data"]["financial_report"]["net_profit_parent"]
```

因此正确的读法必须统一为：

```python
financial_report = (((fctx.get("earnings") or {}).get("data") or {}).get("financial_report") or {})
profit = financial_report.get("net_profit_parent")
report_date = financial_report.get("report_date")
```

### 2.2 `create_all()` 不会给存量 SQLite 表自动补列

当前项目中：

- `Base.metadata.create_all()` 负责建新表；
- 存量表补列依赖 `src/storage.py` 中的 `_ensure_*_columns()` 逻辑。

因此如果 `PricingSnapshot` 新增：

- `sp_ratio`
- `sp_score`

则必须同步在 `src/storage.py` 中新增：

```python
def _ensure_pricing_snapshot_columns(self) -> None:
    ...
```

并在 `DatabaseManager.__init__()` 的初始化链路里执行。

> 不能只要求人工 `ALTER TABLE`，否则方案无法自然接入当前 DSA 的默认启动行为。

### 2.3 板块分母必须基于同一 eligible 集合

初稿将板块总市值与板块总净利润按不同过滤条件分别求和，这会导致 `S` 与 `P` 的分母样本集合不一致。

修正规则：

```python
eligible_stock = total_mv > 0 and net_profit_parent > 0
```

只有 `eligible_stock` 才同时进入：

- `board_total_mktcap`
- `board_total_profit`

否则：

- 当前个股 `sp_ratio = None`
- `status = missing_core_factor`

### 2.4 报告期一致性必须显式定义

当前 `financial_report` 中存在 `report_date`，但现有 phase3 没有财报口径一致性规则。

修正方案定义两个层次：

- **P0（本次采用）**：允许使用各股票“最新已披露”的 `net_profit_parent`，但必须把 `report_date` 记录为批次诊断信息；
- **P1（未来升级）**：按板块统一报告期筛选，仅让同一 `report_date` 的成分股参与 S/P 计算。

本次修正采用 **P0 诊断优先**：

- 读取每股 `report_date`
- 允许混合报告期参与计算
- 但需要在 run 级别至少能看出是否存在 `mixed_profit_report_date`

> 若不记录该信息，后续无法解释同板块内不同股票的 `P` 口径差异。

### 2.5 API 必须暴露 `sp_ratio/sp_score`

如果 `total` 改由 `SP` 主导，但 API 仍只返回：

- `rs_score`
- `cmf`
- `flow_score`

则线上调试与结果解释都不完整。

因此 `api/v1/endpoints/plate_pricing.py` 需要最小补充返回：

- `sp_ratio`
- `sp_score`

### 2.6 测试面必须覆盖 schema / repo / API

仅修改 `tests/test_pricing_service.py` 不够。

至少需要：

- `tests/test_pricing_sp_ratio.py`：SP 因子单测
- `tests/test_pricing_service.py`：主路径与降级逻辑
- `tests/test_pricing_repo.py`：新字段透传 / 排序 / upsert
- `tests/test_plate_pricing_api.py`：API 返回新字段

否则修正方案只在 service 层有测试，无法覆盖 schema / repo / API 的契约闭环。

---

## 3. 修正后的范围与文件矩阵

### 3.1 必改文件（9个）

| 文件 | 动作 | 说明 |
|---|---|---|
| `src/services/pricing/sp_ratio.py` | 新建 | S/P 纯算法 |
| `src/storage.py` | 修改 | `PricingSnapshot` 增列 + SQLite backfill |
| `src/repositories/pricing_repo.py` | 修改 | 透传 `sp_ratio/sp_score` |
| `src/services/pricing_service.py` | 修改 | 主因子从 `RS` 改为 `SP` |
| `api/v1/endpoints/plate_pricing.py` | 修改 | 暴露 `sp_ratio/sp_score` 诊断字段 |
| `tests/test_pricing_sp_ratio.py` | 新建 | SP 因子单测 |
| `tests/test_pricing_service.py` | 修改 | service 场景更新 |
| `tests/test_pricing_repo.py` | 修改/扩展 | repo 新字段、upsert、排序 |
| `tests/test_plate_pricing_api.py` | 修改/扩展 | API 返回新字段 |

### 3.2 不改文件

- `src/services/pricing/cmf.py`
- `src/services/pricing/capital_proxy.py`
- `src/services/pricing/relative_strength.py`
- `src/services/spi/spi_task_runner.py`

这些模块仍可复用，只是角色从主因子降为辅助或诊断。

---

## 4. 数据契约

### 4.1 `PricingSnapshot` 字段扩展

新增：

```python
sp_ratio = Column(Float)   # 原始 S/P，<1 低估，>1 高估
sp_score = Column(Float)   # 1 / (1 + sp_ratio)，高分 = 更低估
```

保留：

```python
rs_score = Column(Float)   # 仍存 20 日动量百分位，仅作诊断
```

### 4.2 存量 SQLite 补列

在 `src/storage.py` 中新增：

```python
def _ensure_pricing_snapshot_columns(self) -> None:
    if not self._is_sqlite_engine:
        return
    existing = {column["name"] for column in inspect(self._engine).get_columns(PricingSnapshot.__tablename__)}
    for name in ("sp_ratio", "sp_score"):
        if name not in existing:
            ALTER TABLE pricing_snapshot ADD COLUMN ...
```

并加入初始化顺序：

```python
Base.metadata.create_all(self._engine)
self._ensure_llm_usage_telemetry_columns()
self._ensure_spi_v2_columns()
self._ensure_pricing_snapshot_columns()
...
```

---

## 5. 因子定义

### 5.1 `SP` 原始比值

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

### 5.2 `SP` 分数映射

```python
def sp_ratio_to_score(sp_ratio):
    return 1 / (1 + sp_ratio)
```

语义：

- `sp_ratio < 1` → `sp_score > 0.5`
- `sp_ratio = 1` → `sp_score = 0.5`
- `sp_ratio > 1` → `sp_score < 0.5`

---

## 6. pricing_service 修正设计

### 6.1 单循环采集数据

在当前成分股循环中，统一收集：

1. `bars` → 计算 `CMF` 与 `RS`
2. `quote = manager.get_realtime_quote(code)` → 读取 `total_mv`
3. `fctx = manager.get_fundamental_context(code)` → 读取 `earnings.data.financial_report.net_profit_parent`
4. `flow = manager.get_capital_flow_context(code)` → 读取主力净流入代理

推荐缓存结构：

```python
raw_rs: List[Optional[float]] = []
raw_cmf: List[Optional[float]] = []
raw_flow: List[Optional[float]] = []
raw_mktcap: List[Optional[float]] = []
raw_profit: List[Optional[float]] = []
raw_profit_report_date: List[Optional[str]] = []
```

### 6.2 运行时成本策略

注意：`get_fundamental_context()` 内部会再次获取 valuation / realtime quote。

本次方案采用 **P0 可落地优先**：

- 保持调用公开方法 `get_realtime_quote()` + `get_fundamental_context()`
- 接受存在一次内部重复 quote 的额外成本
- 不在本次修正中深入重构 `data_provider` 内部契约

后续若要优化性能，再进入 P1：

- 提供轻量 fundamental bundle 直取路径，绕开重复 valuation 获取

### 6.3 eligible 集合与板块分母

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

- 若当前股票不满足 `eligible` → `sp_ratio = None`
- 若板块分母任一 <= 0 → 整批 `sp_ratio = None`

### 6.4 报告期规则

本次采用 **P0 诊断优先规则**：

- 每股读取 `financial_report.report_date`
- 不强制整板统一报告期
- 但必须统计：
  - `distinct_profit_report_dates`
  - `dominant_profit_report_date`
  - 是否 `mixed_profit_report_date`

由于当前 `PricingFactorRun` 没有 metadata JSON 字段，P0 可先：

- 在日志中输出；
- 或在 `error` 为空时不强行塞入报告期信息；
- 后续若需要长期诊断，再给 `PricingFactorRun` 扩展 metadata 字段。

---

## 7. 总分合成

### 7.1 权重

```python
_BASE_W_SP = 0.6
_BASE_W_CMF = 0.3
_BASE_W_FLOW = 0.1

_BASE_W_SP_NO_FLOW = 0.667
_BASE_W_CMF_NO_FLOW = 0.333
```

### 7.2 `status` / `total` 规则

| SP | CMF | Flow | 单股 status | total |
|---|---|---|---|---|
| None | any | any | `missing_core_factor` | None |
| ok | None | any | `missing_core_factor` | None |
| ok | ok | ok（`flow_enabled`） | `ok` | `0.6*sp + 0.3*cmf + 0.1*flow` |
| ok | ok | None（`flow_enabled` 单只缺失） | `degraded` | `0.667*sp + 0.333*cmf` |
| ok | ok | —（`flow_disabled`） | `ok` | `0.667*sp + 0.333*cmf` |

修正点：

- `rs_score` 继续计算并写入快照，但不参与主 `total`
- `cmf_score` 仍用 `(cmf + 1) / 2`
- `flow_score` 仍按板块内百分位

### 7.3 `factor_mask` 修正

原先：

- `rs,cmf,flow`
- `rs,cmf`

修正后应改为：

- `sp,cmf,flow`
- `sp,cmf`
- 缺核心因子则 `None`

---

## 8. repo 与 API 修正

### 8.1 `pricing_repo.py`

需要透传：

- `sp_ratio`
- `sp_score`

改动点：

- `upsert_pricing()`
- `_upsert_pricing_in_session()`
- `save_pricing_batch()` snapshots 透传

### 8.2 `plate_pricing.py`

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

这样既保留原动量诊断，也把新的 S/P 主因子显式暴露给调用方。

---

## 9. 测试闭环

### 9.1 新增：`tests/test_pricing_sp_ratio.py`

覆盖：

- `SP < 1` → `score > 0.5`
- `SP = 1` → `score = 0.5`
- `SP > 1` → `score < 0.5`
- 任一参数 None → None
- `stock_profit <= 0` → None
- `board_total_profit <= 0` → None
- `board_total_mktcap <= 0` → None

### 9.2 修改：`tests/test_pricing_service.py`

补充：

- `SP None → missing_core_factor`
- `SP + CMF 正常, flow_disabled → total 仅由 SP/CMF 构成`
- `rs_score` 保留写入但不参与 `total`
- `factor_mask` 从 `rs,...` 切到 `sp,...`

### 9.3 修改：`tests/test_pricing_repo.py`

补充：

- `sp_ratio/sp_score` 成功落库
- same-day upsert 后新值覆盖旧值
- 排序仍按 `total` 进行

### 9.4 修改：`tests/test_plate_pricing_api.py`

补充：

- 返回 `sp_ratio/sp_score`
- 显式 `trade_date` 与 latest trade date 默认路径保持兼容

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

该修正方案在当前仓库内**可实施**，前提是接受以下边界：

1. `P` 固定使用 `net_profit_parent`，不做营收 fallback；因此部分股票会被自然筛掉；
2. P0 先接受“最新已披露财报混合”的现实，但要把混合报告期作为诊断显式化；
3. 运行成本会高于当前动量版 phase3，但仍在可接受范围内；
4. 若不补 schema backfill / API 诊断字段 / repo/API 测试，本方案不算完整落地。

一句话总结：

> 可行，但必须把“真实数据路径、SQLite 补列、同一 eligible 集合、报告期诊断、API 暴露、测试闭环”六个点一起修正，才能从概念方案变成可执行方案。
