# AlphaSift SPI 集成 - 快速参考卡

> **版本**: v1.1  
> **日期**: 2026-07-04  
> **用途**: 实现 `SectorRotationScreener` / `PricingFilter` 时的快速查阅手册

---

## 1. 核心因子（Phase3 实际实现）

| 因子 | 权重 | 计算方式 | 取值范围 | 业务含义 |
|------|------|---------|---------|---------|
| **SP** | 60% | `sp_ratio = (个股市值占比) / (个股利润占比)`<br>`sp_score = 1 / (1 + sp_ratio)` | sp_ratio: [0, +∞)<br>sp_score: (0, 1] | sp_ratio>1 估值偏高<br>sp_ratio<1 估值偏低 |
| **CMF** | 30% | Chaikin Money Flow（原始值） | [-1, 1] | 正值：资金流入<br>负值：资金流出 |
| **Flow** | 10% | legulegu 主力资金代理 | [0, 1] | 可选，覆盖率≥60%时启用 |

**降级规则**：Flow 覆盖率 < 60% 时，切换为双因子（SP 66.7%, CMF 33.3%）

---

## 2. 字段契约速查

### 2.1 PricingSnapshot 表字段（实际存储）

| 字段 | 类型 | 说明 | 是否进评分 |
|------|------|------|-----------|
| `stock_code` | String | 股票代码 | - |
| `total` | Float [0,1] \| null | 综合分（主排序） | ✅ 最终分数 |
| `sp_ratio` | Float \| null | 市值盈利比价原始值 | 转换为 sp_score |
| `sp_score` | Float [0,1] \| null | SP 归一化分 | ✅ 主因子 60% |
| `cmf` | Float [-1,1] \| null | CMF 原始值（未归一化） | 归一化后 30% |
| `flow_score` | Float [0,1] \| null | 资金流分 | ✅ 辅助 10% |
| `rs_score` | Float [0,1] \| null | 相对强弱（诊断字段） | ❌ 不进评分 |
| `status` | String | `ok` / `degraded` / `missing_core_factor` | - |
| `factor_mask` | String | 参与计算的因子，如 `sp,cmf,flow` | - |

### 2.2 API 返回字段（集成层增强）

```json
{
  "code": "600519",
  "board_id": 801010,
  "board_name": "食品饮料",
  "board_v2_score": 85.3,
  "total": 0.78,
  "pricing_rank": 1,
  "sp_ratio": 0.85,
  "sp_score": 0.54,
  "cmf": 0.15,
  "flow_score": 0.65,
  "status": "ok",
  "factor_mask": "sp,cmf,flow",
  "selection_reason": "板块轮动BUY信号"
}
```

**注**：`pricing_rank` 由集成层计算，不在表中。

---

## 3. 调用接口速查

### 3.1 查询板块内比价排名

```python
from src.repositories.pricing_repo import PricingRepository

repo = PricingRepository()
rows = repo.find_board_pricing_rank(
    board_id=801010,
    trade_date=date(2026, 7, 4)
)

# 返回：List[PricingSnapshot]
# 字段：stock_code, total, sp_ratio, sp_score, cmf, flow_score, status, ...
```

### 3.2 计算 pricing_rank

```python
# PricingSnapshot 表无 rank 字段，需自行计算
ranked = sorted(rows, key=lambda r: r.total or 0, reverse=True)

for idx, row in enumerate(ranked, start=1):
    result.append({
        "stock_code": row.stock_code,
        "total": row.total,
        "pricing_rank": idx,  # ← 自行计算
        # ...
    })
```

### 3.3 查询轮动信号

```python
from src.repositories.spi_repo import PlateSpiRepository

repo = PlateSpiRepository()
signals = repo.find_rotation_signals(
    trade_date=date(2026, 7, 4),
    action="BUY"
)

# 返回：List[SpiRotationSignal]
# 字段：board_id, stock_code, reason, ...
```

### 3.4 查询板块 v2 评分

```python
boards = repo.find_top_boards_v2(
    date=date(2026, 7, 4),
    top_n=50  # 可选，不传则返回所有
)

# 返回：List[PlateSpiSnapshot]
# 字段：board_id, board_name, v2_score, ...
```

---

## 4. 历史 Fallback 规则

| 场景 | trade_date | 行为 | 数据源 |
|------|-----------|------|--------|
| **当日查询（快照存在）** | `spi_time()` | 返回快照数据 | `constituent_snapshot` 表 |
| **当日查询（快照缺失）** | `spi_time()` | 实时抓取 legulegu | `ConstituentFetcher.fetch()` |
| **历史查询（快照存在）** | 历史日期 | 返回快照数据 | `constituent_snapshot` 表 |
| **历史查询（快照缺失）** | 历史日期 | **返回空 + 源标记 "missing"** | ❌ 禁止 fallback |

**代码位置**：`src/services/pricing_service.py:119-120`

```python
if trade_date != spi_time():
    return [], "missing"  # 历史日期禁止 fallback
```

---

## 5. 常见陷阱

### ❌ 错误示例

```python
# 错误1：假设存在 Service 层方法
pricing_ranks = pricing_service.rank_stocks_in_board(board_id, date)
# ↑ 不存在！应使用 PricingRepository.find_board_pricing_rank()

# 错误2：期望返回 rank 字段
for row in rows:
    print(row.rank)  # ← AttributeError! 无此字段
# ↑ 需自行排序计算

# 错误3：假设 cmf 已归一化
cmf_score = row.cmf  # ← 原始值 [-1,1]，未归一化
# ↑ 需手动转换：(cmf + 1) / 2

# 错误4：历史日期期望 fallback
rows = repo.find_board_pricing_rank(board_id, date(2025, 1, 1))
# ↑ 若无快照，返回空（不会自动抓取 current）
```

### ✅ 正确示例

```python
from src.repositories.pricing_repo import PricingRepository
from datetime import date

repo = PricingRepository()

# 1. 查询比价快照
rows = repo.find_board_pricing_rank(
    board_id=801010,
    trade_date=date(2026, 7, 4)
)

# 2. 按 total DESC 排序并计算 rank
ranked = sorted(
    [r for r in rows if r.total is not None],
    key=lambda r: r.total,
    reverse=True
)

# 3. 构建返回结果
results = []
for idx, row in enumerate(ranked, start=1):
    results.append({
        "stock_code": row.stock_code,
        "total": row.total,
        "pricing_rank": idx,
        "sp_ratio": row.sp_ratio,
        "sp_score": row.sp_score,
        "cmf": row.cmf,  # 原始值，前端可选归一化
        "flow_score": row.flow_score,
        "status": row.status,
    })
```

---

## 6. 伪代码模板

### 6.1 板块轮动选股器核心逻辑

```python
def screen_sector_rotation(market, max_results, trade_date):
    # Step 1: 获取 BUY 信号
    signals = spi_repo.find_rotation_signals(trade_date, action="BUY")
    if not signals:
        return {"candidates": [], "warnings": ["无BUY信号"]}
    
    # Step 2: 按板块分组
    board_stocks = defaultdict(list)
    for sig in signals:
        board_stocks[sig.board_id].append(sig.stock_code)
    
    # Step 3: 获取板块 v2 评分
    boards = spi_repo.find_top_boards_v2(date=trade_date)
    board_map = {b.board_id: b for b in boards}
    
    # Step 4: 板块内选股
    candidates = []
    for board_id, stock_codes in board_stocks.items():
        # 查询比价快照
        pricing_rows = pricing_repo.find_board_pricing_rank(
            board_id=board_id,
            trade_date=trade_date
        )
        
        # 过滤信号涉及的个股 + 按 total 排序
        filtered = [r for r in pricing_rows if r.stock_code in stock_codes]
        ranked = sorted(filtered, key=lambda r: r.total or 0, reverse=True)[:5]
        
        # 构建候选
        board = board_map.get(board_id)
        for r in ranked:
            candidates.append({
                "code": r.stock_code,
                "board_id": board_id,
                "board_name": board.board_name if board else None,
                "board_v2_score": board.v2_score if board else None,
                "total": r.total,
                "sp_score": r.sp_score,
                # ...
            })
    
    # Step 5: 按板块 v2 降序
    candidates.sort(key=lambda x: x.get("board_v2_score") or 0, reverse=True)
    
    return {"candidates": candidates[:max_results]}
```

### 6.2 比价过滤器核心逻辑

```python
def apply_pricing_filter(candidates, min_total=0.5, trade_date):
    # Step 1: 按板块分组
    board_groups = defaultdict(list)
    for c in candidates:
        board_groups[c["board_id"]].append(c)
    
    # Step 2: 逐板块增强
    enriched = []
    for board_id, group in board_groups.items():
        # 查询比价快照
        rows = pricing_repo.find_board_pricing_rank(board_id, trade_date)
        
        # 排序并计算 rank
        ranked = sorted(rows, key=lambda r: r.total or 0, reverse=True)
        pricing_map = {
            r.stock_code: {"total": r.total, "rank": idx + 1, ...}
            for idx, r in enumerate(ranked)
        }
        
        # 匹配并过滤
        for candidate in group:
            pricing = pricing_map.get(candidate["code"])
            if pricing:
                candidate.update(pricing)
                # 过滤低分
                if pricing["total"] and pricing["total"] < min_total:
                    continue
            enriched.append(candidate)
    
    # Step 3: 重排序
    enriched.sort(key=lambda x: x.get("total") or 0, reverse=True)
    return enriched
```

---

## 7. 测试用 SQL

```sql
-- 检查 SPI v2 数据是否就绪
SELECT COUNT(*) FROM plate_spi_snapshot 
WHERE v2_score IS NOT NULL AND trade_date = '2026-07-04';

-- 检查轮动信号
SELECT board_id, COUNT(*) as stock_count 
FROM spi_rotation_signal 
WHERE action = 'BUY' AND trade_date = '2026-07-04'
GROUP BY board_id;

-- 检查比价快照
SELECT board_id, COUNT(*) as priced_count,
       SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) as ok_count
FROM pricing_snapshot
WHERE trade_date = '2026-07-04'
GROUP BY board_id;

-- 查看某板块比价排名（Top 5）
SELECT stock_code, total, sp_score, cmf, flow_score, status
FROM pricing_snapshot
WHERE board_id = 801010 AND trade_date = '2026-07-04'
ORDER BY total DESC NULLS LAST
LIMIT 5;
```

---

## 8. 快速诊断清单

| 问题 | 检查项 | 修复方向 |
|------|--------|---------|
| **无候选股返回** | 1. 轮动信号是否存在<br>2. v2 快照是否就绪<br>3. 比价快照是否存在 | 运行日终任务 |
| **total 为 null** | 1. status 字段值<br>2. factor_mask 字段 | 检查 SP/CMF 原始数据 |
| **flow_score 全为 null** | Flow 覆盖率是否 < 60% | 正常降级为双因子 |
| **历史日期无数据** | 成分股快照是否存在 | 补历史快照或只用有快照的日期 |
| **sp_ratio 为负** | 净利润为负的个股 | sp_score 会非常小（接近0） |

---

**文档维护**：本速查卡基于 v1.1 集成文档生成，与实际代码对齐。如有疑问，优先参考源码。
