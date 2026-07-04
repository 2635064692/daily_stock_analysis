# SPI 阶段3 比价系统 P0 — Implementation Tasks

> Change ID: spi-rotation-phase3
> currentPhase: 6-validation
> 策略: full-collaborate。实现已完成，P1/P2 review blockers 已修复并完成定向验证。
>
> **并行语义说明**：同一 Parallel ID（pN）的 task 跨 Phase 块可并行执行。
> 每个因子模块与其单测共享 Parallel ID（如 cmf.py 与 test_cmf.py 同为 p2），
> 模块完成后立即触发对应测试，实现"模块+测试"链路级并行，而非"所有模块串行完再统一测试"。

---

### Phase 1: 基础层（无依赖起点）

- **current_phase**: `3`
- **task_phase**: `1`
- **Parallel ID**: `p1`
- **depends-on**: 无

- [×] **1.1** storage.py 加 PricingSnapshot + PricingFactorRun 表（Base.metadata.create_all） — depends-on: 无
- [×] **1.2** src/services/pricing/cmf.py：calc_cmf(20) + normalize_cmf，high==low→0、Σvol=0→None、min_period=5 — depends-on: 无
- [×] **1.3** src/services/pricing/relative_strength.py：calc_period_return + calc_rs_scores_nullable（板块内百分位，n<=1→0.5） — depends-on: 无
- [×] **1.4** src/services/pricing/capital_proxy.py：extract_flow(main_net_inflow优先) + normalize_flow_scores(板块百分位) — depends-on: 无
- [×] **1.5** src/repositories/pricing_repo.py：upsert_pricing + insert_factor_run + find_board_pricing_rank + find_latest_trade_date — depends-on: 1.1

---

### Phase 2: 因子单测（与 Phase 1 同 Parallel ID，模块完成即并行触发）

> （cmf/rs/capital_proxy 三条"模块+测试"链路与 Phase 1 并行推进）

- **current_phase**: `3`
- **task_phase**: `2`
- **Parallel ID**: `p1`
- **depends-on**: 无

- [×] **2.1** tests/test_pricing_cmf.py：已知K线断言 + 边界（high==low/Σvol=0/不足N） — depends-on: 1.2
- [×] **2.2** tests/test_pricing_relative_strength.py：构造收益 + 并列名次 + n<=1→0.5 + 缺位保留 — depends-on: 1.3
- [×] **2.3** tests/test_pricing_capital_proxy.py：字段优先级 + 全空None + 板块百分位 — depends-on: 1.4

---

### Phase 3: 编排服务（依赖三因子 + repo）

- **current_phase**: `3`
- **task_phase**: `3`
- **Parallel ID**: ``
- **depends-on**: p1

- [×] **3.1** src/services/pricing_service.py：PricingService.price_board 批次级动态重归一化（串行，legulegu 0.5s sleep） — depends-on: 1.2,1.3,1.4,1.5
- [×] **3.2** tests/test_pricing_service.py：mock4组件 + flow_coverage切换/缺CMF/当日fetch/历史missing — depends-on: 3.1

---

### Phase 4: 接入层（依赖 pricing_service，API 与日终接入可并行）

> （API 与日终接入两条链路并行）

- **current_phase**: `3`
- **task_phase**: `4`
- **Parallel ID**: `p2`
- **depends-on**: Phase 3

- [×] **4.1** api/v1/endpoints/plate_pricing.py：GET /board/{id}/pricing（只读库）+ router.py 注册 — depends-on: 3.1
- [×] **4.2** spi_task_runner.refresh_daily 链尾追加 price_board（find_top_boards_v2 top30，独立 try/except） — depends-on: 3.1

---

## 总计

**Total Tasks**: 11

**并行结构**：
- **Phase 1+2（p1）**：5 模块 + 3 单测，cmf/rs/capital_proxy 三条"模块→测试"链路并行；storage→repo 串行子链
- **Phase 3**：pricing_service（依赖三因子+repo）→ 其测试，串行
- **Phase 4（p2）**：API 与日终接入并行，均只依赖 pricing_service

**关键约束**：
- 同 Parallel ID 跨 Phase 块并行；不同 Parallel ID（p1→串行→Phase3→p2）按依赖串行
- pricing_service 内部 price_board 串行执行（legulegu 0.5s sleep，禁并发）
