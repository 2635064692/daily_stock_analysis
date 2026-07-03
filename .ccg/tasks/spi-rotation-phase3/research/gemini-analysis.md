# Gemini Analyzer — phase3 比价系统规划审计

> Session-ID: fc0db71d-e326-4c61-a5f7-9621f4ec98cf
> 模型: gemini-2.5-pro
> 角色: analyzer（UI/UX 视角，但产出按后端规划回应）

## A. CMF(21) 算法契约
- 公式：`MFM = [(Close-Low)-(High-Close)]/(High-Low)`，`MFV = MFM*Volume`，`CMF(21) = Sum(MFV,21)/Sum(Volume,21)`
- high==low → MFM=0；Σvolume=0 → CMF=0；不足21根按实际N算，N<5 置 None
- 复权一致性：H/L/C 必须同一前复权(qfq)
- 伪代码（见原文，pandas 风格）

## B. RS 归一化
- 推荐**板块指数基准**（AkshareSwAdapter 已就位，O(1) vs O(M)）
- N=21（与 CMF 对齐）
- 百分位：`RS_norm = (Rank-1)/(M-1)`，M==1 → 0.5

## C. Flow 降级
- 仅当日增强，历史回算跳过
- 失败置 None，交由权重层动态消化

## D. 动态重归一化
- Flow 缺失：RS 0.5→0.625，CMF 0.3→0.375

## E. phase2 依赖
- **推荐方案(b)**：get_constituents 缺失时 fallback 取 latest，phase2 修复后自动升级 point-in-time

## F. 表设计 stock_pricing_snapshot
- 字段：trade_date, board_id, stock_code, rs_raw, rs_norm, cmf_21, cmf_norm, flow_value, flow_norm, total_score, UNIQUE(trade_date,board_id,stock_code)
- ⚠️ 写了 "Alembic migration"（与 DSA 实际用 Base.metadata.create_all() 不符，契约漂移）

## G. WBS 3.0-3.8（含 3.5 快照降级层、3.7 日终接入）

## H. 远端验收（sqlite 查表 + curl + 日志 grep）

## ⚠️ Gemini 的盲点（Codex 已指出）
1. CMF 写 21 但未交叉验证业界标准（StockCharts/pandas_ta 默认 20）
2. 未发现 §24.3 旧字段 super_large_net 与 H-3 真实字段 main_net_inflow 的矛盾
3. 未发现 RS "板块内 rank" 框架下 "减板块指数" 对排序不生效（数学冗余）
4. flow_norm 用"板块内排名"，但 Flow 是 T-0、历史日全缺，排名无意义
5. Alembic 漂移
