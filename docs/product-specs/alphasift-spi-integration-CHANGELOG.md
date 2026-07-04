# AlphaSift SPI 集成文档变更日志

## v1.1 (2026-07-04) - 对齐实际实现

### 修订背景

文档 v1.0 基于早期设计编写，与 Phase3 实际实现存在偏差。v1.1 修订对齐当前真实代码契约，确保文档准确性。

---

## 主要变更

### 1. **实现状态声明** ✅ 新增

在文档头部增加实现状态说明：

```
**实现状态**：
- ✅ SPI 数据层：v1/v2 快照、轮动信号、比价快照已完成
- ✅ 日终任务链：v1 → v2 → rotation → pricing 顺序已落地
- ✅ AlphaSift 集成层：`sector_rotation` 路由、`pricing_filter` 后处理器已落地
```

**影响**：明确告知读者当前文档为"集成蓝图"，AlphaSift API 层尚未实现。

---

### 2. **比价因子从 RS 切换为 SP** 🔄 核心变更

#### 变更前（v1.0）

> 比价增强：在板块内按**相对强弱 + CMF + 资金流**排序

#### 变更后（v1.1）

> 比价增强：在板块内按 **SP（市值盈利比价）+ CMF + 资金流** 排序

#### 因子说明

| 因子 | 计算方式 | 权重 | 业务含义 |
|------|---------|------|---------|
| **SP** | `sp_ratio = (个股市值/板块总市值) / (个股净利润/板块总利润)`<br>`sp_score = 1 / (1 + sp_ratio)` | 60% | 估值相对板块内高低 |
| **CMF** | Chaikin Money Flow [-1, 1] | 30% | 技术面资金流向 |
| **Flow** | legulegu 主力资金数据 | 10%（可选） | 主力资金净流入 |

**历史说明**：早期设计曾考虑 RS（相对强弱），Phase3 实际实现切换为 SP 因子。RS 保留为诊断字段。

**影响范围**：
- 文档第 1.2 节核心价值描述
- 流程图（数据依赖关系图）
- 伪代码实现
- FAQ 权重说明

---

### 3. **字段契约对齐** 🔧 接口变更

#### 变更前（v1.0）

```json
{
  "pricing_score": 0.92,
  "pricing_rank": 1,
  "rs_score": 0.85,
  "cmf_score": 0.72,
  "flow_score": 0.65
}
```

#### 变更后（v1.1）

```json
{
  "total": 0.78,
  "pricing_rank": 1,
  "sp_ratio": 0.85,
  "sp_score": 0.54,
  "cmf": 0.15,
  "flow_score": 0.65,
  "status": "ok",
  "factor_mask": "sp,cmf,flow"
}
```

#### 详细映射

| v1.0 字段 | v1.1 字段 | 类型 | 说明 |
|----------|----------|------|------|
| `pricing_score` | `total` | Float [0,1] | 主排序字段，重命名为 `total` |
| `pricing_rank` | `pricing_rank` | Integer | ✅ 保持不变（集成层计算） |
| `rs_score` | ❌ 移除 | - | RS 降级为诊断字段，不展示 |
| `cmf_score` | `cmf` | Float [-1,1] | 原始值（未归一化） |
| `flow_score` | `flow_score` | Float [0,1] | ✅ 保持不变 |
| - | `sp_ratio` | Float | ✅ 新增（原始比价值） |
| - | `sp_score` | Float [0,1] | ✅ 新增（归一化分） |
| - | `status` | String | ✅ 新增（ok/degraded/missing_core_factor） |
| - | `factor_mask` | String | ✅ 新增（如 "sp,cmf,flow"） |

**注**：`pricing_rank` 不在 `PricingSnapshot` 表中，由集成层按 `total DESC` 计算。

---

### 4. **调用接口修正** 🔄 实现细节

#### 变更前（v1.0）

```python
# 文档假设有 Service 层方法
pricing_ranks = pricing_service.rank_stocks_in_board(board_id, trade_date)
# 返回：[{stock_code, total_score, rank}]
```

#### 变更后（v1.1）

```python
# 实际使用 Repository 层查询
from src.repositories.pricing_repo import PricingRepository
repo = PricingRepository()
rows = repo.find_board_pricing_rank(board_id, trade_date)

# 返回：[PricingSnapshot(stock_code, total, sp_ratio, sp_score, cmf, ...)]
# 无 rank 字段，需自行排序计算
ranked = sorted(rows, key=lambda r: r.total or 0, reverse=True)
for idx, row in enumerate(ranked, start=1):
    row.rank = idx
```

**影响**：
- 伪代码实现（`SectorRotationScreener` / `PricingFilter`）
- API 响应示例
- 技术细节文档算法部分

---

### 5. **历史 Fallback 策略修正** ⚠️ 行为变更

#### 变更前（v1.0）

> 板块内排序：成分股快照 + 比价快照 | 使用 current snapshot（有生存者偏差）

#### 变更后（v1.1）

> 板块内排序（当日）：成分股快照 + 比价快照 | 实时抓取 legulegu current snapshot（仅当日允许）  
> 板块内排序（历史）：成分股快照 + 比价快照 | 返回空（禁止 fallback，保护 point-in-time 语义）

**代码依据**：

```python
# src/services/pricing_service.py:119-120
if trade_date != spi_time():
    return [], "missing"  # 历史日期禁止 fallback
```

**影响**：
- 数据依赖与校验（9.1 前置条件检查矩阵）
- 降级策略（11.1 降级策略表格）
- FAQ Q3（历史回测说明）

---

### 6. **新增字段映射参考表** ✅ 新增

在技术细节文档 7.3 节增加完整的字段契约映射表，明确：
- 实际 PricingSnapshot 字段
- 是否进主评分
- 数据类型与取值范围

---

### 7. **新增已实现 API 说明** ✅ 新增

在集成文档 7.4 节增加已落地的比价查询接口：

```http
GET /api/v1/plate-pricing/board/{board_id}/pricing?trade_date=2026-07-04
```

返回字段与实际实现完全一致。

---

## 未变更部分

以下设计保持不变：
- ✅ 整体架构设计（策略路由 + 后处理器）
- ✅ 流程图（除因子名称外）
- ✅ 日终任务链（v1 → v2 → rotation → pricing）
- ✅ 验收标准
- ✅ 监控指标
- ✅ 发布计划

---

## 迁移指南

### 对于集成开发者

如果你已基于 v1.0 文档开始实现 `SectorRotationScreener` 或 `PricingFilter`，需要调整：

1. **字段重命名**

```diff
- candidate.pricing_score = pricing.total_score
+ candidate.total = pricing.total

- candidate.rs_score = pricing.rs_score
+ # RS 不展示，移除此字段

- candidate.cmf_score = pricing.cmf_score  # 归一化值
+ candidate.cmf = pricing.cmf  # 原始值 [-1,1]
```

2. **调用接口切换**

```diff
- pricing_ranks = pricing_service.rank_stocks_in_board(board_id, trade_date)
+ from src.repositories.pricing_repo import PricingRepository
+ repo = PricingRepository()
+ rows = repo.find_board_pricing_rank(board_id, trade_date)
+ # 需自行计算 rank
```

3. **历史降级逻辑**

```diff
- # 历史日期允许 fallback 到 current snapshot
+ # 历史日期禁止 fallback，返回空
+ if trade_date != spi_time() and not has_snapshot:
+     return [], "missing"
```

### 对于 API 消费者

如果你已基于 v1.0 响应格式编写前端代码，需要调整：

```diff
- const score = candidate.pricing_score;
+ const score = candidate.total;

- const cmfNormalized = candidate.cmf_score;  // [0,1]
+ const cmfRaw = candidate.cmf;  // [-1,1]
+ const cmfNormalized = (cmfRaw + 1) / 2;  // 手动归一化

+ // 新增字段
+ const spRatio = candidate.sp_ratio;
+ const spScore = candidate.sp_score;
+ const status = candidate.status;
```

---

## 验证检查清单

修订后文档已对齐以下实际代码：

- [x] `src/services/pricing_service.py:23-27` 权重常量（SP 60%, CMF 30%, Flow 10%）
- [x] `src/services/pricing_service.py:278-283` 快照字段（total, sp_ratio, sp_score, cmf, flow_score）
- [x] `api/v1/endpoints/plate_pricing.py:35-45` API 返回字段
- [x] `src/repositories/pricing_repo.py:150` 查询方法 `find_board_pricing_rank()`
- [x] `src/services/pricing_service.py:119-120` 历史 fallback 禁止逻辑

---

## 后续工作

v1.1 文档与当前仓库实现已对齐，后续重点转为数据就绪后的联调与验收：

1. **P1**：在目标环境补齐 SPI v2 / BUY 信号 / 比价快照数据
2. **P1**：执行 `sector_rotation` 实数仓联调与字段契约验收
3. **P2**：补充端到端回归证据（API 返回候选字段与排序结果）

---

**文档维护原则**：本文档随代码实际实现同步更新。字段契约、调用接口以实际代码为准。
