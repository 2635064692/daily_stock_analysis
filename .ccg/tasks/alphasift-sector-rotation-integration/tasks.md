# AlphaSift 板块轮动集成 — Implementation Tasks

> Change ID: alphasift-sector-rotation-integration
> Branch: docs/grok-search-and-alphasift
> Strategy: guided-develop
> currentPhase: 6

---

### Phase 1: 核心选股器实现

- **current_phase**: `1`
- **task_phase**: `1`
- **Parallel ID**: ``
- **depends-on**: 无

- [x] **1.1** 创建 `src/services/spi/rotation_screening.py` 文件骨架 — depends-on: 无
- [x] **1.2** 实现 `SectorRotationScreener.screen()` 核心逻辑 — depends-on: 1.1
- [x] **1.3** 实现板块内比价排序逻辑（调用 `PricingRepository`）— depends-on: 1.2
- [x] **1.4** 实现候选股汇总和排序（按板块 v2 降序）— depends-on: 1.3

---

### Phase 2: 比价过滤器实现

- **current_phase**: `1`
- **task_phase**: `2`
- **Parallel ID**: ``
- **depends-on**: 无

- [x] **2.1** 创建 `src/services/spi/pricing_filter.py` 文件骨架 — depends-on: 无
- [x] **2.2** 实现 `PricingFilter.apply()` 核心逻辑 — depends-on: 2.1
- [x] **2.3** 实现板块分组和比价快照匹配 — depends-on: 2.2
- [x] **2.4** 实现过滤和重排序逻辑 — depends-on: 2.3

---

### Phase 3: AlphaSift 服务集成

- **current_phase**: `2`
- **task_phase**: `3`
- **Parallel ID**: ``
- **depends-on**: 无

- [x] **3.1** 在 `AlphaSiftService.screen()` 中增加 `sector_rotation` 路由分支 — depends-on: 无
- [x] **3.2** 集成 `SectorRotationScreener` 调用 — depends-on: 3.1
- [x] **3.3** 集成 `PricingFilter` 可选后处理（enable_pricing_filter 参数）— depends-on: 3.2
- [x] **3.4** 注册 `sector_rotation` 到策略列表 `_list_strategies()` — depends-on: 3.2

---

### Phase 4: API 层扩展

- **current_phase**: `2`
- **task_phase**: `4`
- **Parallel ID**: ``
- **depends-on**: 无

- [x] **4.1** 在 `AlphaSiftScreenRequest` Schema 中增加 `enable_pricing_filter` 可选字段 — depends-on: 无
- [x] **4.2** 更新 API endpoint 传递新参数到 Service 层 — depends-on: 4.1

---

### Phase 5: 单元测试

- **current_phase**: `3`
- **task_phase**: `5`
- **Parallel ID**: ``
- **depends-on**: 无

- [x] **5.1** 创建 `tests/test_rotation_screening.py` — depends-on: 无
- [x] **5.2** 测试无信号场景 (`test_screen_no_signals`) — depends-on: 5.1
- [x] **5.3** 测试有信号场景 (`test_screen_with_signals`) — depends-on: 5.1
- [x] **5.4** 测试比价排序启用/禁用 (`test_screen_with_pricing` / `test_screen_without_pricing`) — depends-on: 5.1
- [x] **5.5** 创建 `tests/test_pricing_filter.py` — depends-on: 无
- [x] **5.6** 测试基础过滤 (`test_apply_filter_basic`) — depends-on: 5.5
- [x] **5.7** 测试阈值过滤 (`test_apply_filter_threshold`) — depends-on: 5.5
- [x] **5.8** 测试比价缺失场景 (`test_apply_missing_pricing`) — depends-on: 5.5

---

### Phase 6: 集成测试与验证

- **current_phase**: `3`
- **task_phase**: `6`
- **Parallel ID**: ``
- **depends-on**: 无

- [x] **6.1** 验证 SPI v2 数据就绪（SQL 检查；本地 `data/stock_analysis.db` 最新交易日 `2026-07-02`，`v2_score` 非空记录数为 `0`）— depends-on: 无
- [x] **6.2** 验证轮动信号存在（SQL 检查；本地 BUY 信号记录数为 `0`）— depends-on: 无
- [x] **6.3** 验证比价快照存在（SQL 检查；本地 `pricing_snapshot` 记录数为 `0`）— depends-on: 无
- [x] **6.4** API 端到端测试：`POST /api/v1/alphasift/screen` with `sector_rotation`（本地 smoke 返回空候选 + `无BUY信号`）— depends-on: 6.1, 6.2, 6.3
- [x] **6.5** API 端到端测试：`enable_pricing_filter=true`（本地 smoke 返回 `pricing_filter_enabled=true` 且 `after_filter_count=0`）— depends-on: 6.4
- [ ] **6.6** 验证响应字段契约（total, sp_ratio, sp_score, cmf, flow_score 等）— depends-on: 6.4

> Takeover audit（2026-07-04）：
> - Claude 已完成 Phase1/2 主体实现，但我接管后补上了 `PricingFilter` 默认日期 bug、`sector_rotation` 策略注册、`enable_pricing_filter` 服务层收口与缺失测试。
> - Phase6 本地数据未就绪：当前库缺少 `v2_score`/BUY 信号/比价快照，因此只能完成空数据 smoke，候选字段契约仍需在数据就绪后补验。

---

## 总计

**Total Tasks**: 28 tasks
**Phases**: 6 phases
**Estimated Lines**: ~530 lines (200 + 150 + 80 + 5 + 15 + ~80 tests)
