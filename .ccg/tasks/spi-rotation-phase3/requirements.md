# phase3 比价系统 P0 — Requirements

> 来源：docs/product-specs/spi-rotation-pricing-requirement-boundary.md §22–§28（DR1–DR10 已冻结）
> 规划阶段产出，复用进 full-collaborate Phase 1。

## 目标
板块内个股比价系统 P0 = same-day 板块内比价排名。
CMF(20) + RS(20) + Flow（best-effort）三因子 → 板块内个股排序 → PricingSnapshot 落库 + GET /pricing API + 日终接入。

## 范围（WBS 3.0–3.8，全选）
- 3.0 storage：PricingSnapshot + PricingFactorRun 表（Base.metadata.create_all，无 alembic）
- 3.1 cmf.py：CMF(20)，high==low→0、Σvol=0→None、min_period=5、复权固定
- 3.2 relative_strength.py：20日收益板块内百分位（不减指数），n<=1→0.5
- 3.3 capital_proxy.py：适配 main_net_inflow 等真实字段（只读），fail-open→None
- 3.4 pricing_service.py：批次级动态重归一化 + cmf_score=(cmf+1)/2 + RS/CMF 缺→degraded
- 3.5 pricing_repo.py：upsert + PricingFactorRun（无 UNIQUE）+ 板块内排名查询
- 3.6 成分股读路径：get_constituents 缺失 fallback latest
- 3.7 API plate_pricing.py GET /board/{id}/pricing + refresh_daily 链尾独立 try/except
- 3.8 远端验收（本机仅 py_compile/flake8/pytest）

## 约束
- 零侵入 get_sector_rankings/get_belong_boards/capital_flow_context
- 复用 StockRepository.get_range / AkshareSwAdapter.get_index_kline / ConstituentSnapshotRepo
- 精简无冗余，非必要不写注释文档
- 新建为主（src/services/pricing/ + src/repositories/pricing_repo.py + api/v1/endpoints/plate_pricing.py）

## 验收（DoD）
- 三因子单测：CMF 停牌/零量/不足N 不抛错 None 语义正确；RS rank∈[0,1] n<=1→0.5；Flow 断网→None
- pricing_service：Flow 整批禁用时 RS0.625/CMF0.375；RS/CMF 缺→degraded
- upsert 幂等；API 返回板块内排序；日终异常隔离不拖垮主链
- py_compile + flake8 + pytest -m "not network" 通过

## 需求完整性评分：9/10
- 目标明确性 3/3、预期结果 3/3、边界范围 2/2、约束条件 1/2（flow_coverage 阈值 0.6 为经验值，无回测）
