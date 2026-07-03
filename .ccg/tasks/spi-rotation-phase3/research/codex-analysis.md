# Codex Analyzer — phase3 比价系统规划审计

> Session-ID: 019f2809-8744-70c2-b4f3-7943715b907e
> 模型: codex（含 web_search 交叉验证 CMF 业界标准）
> 深度: 118 events，逐文件源码定位 + 复权链路 + capital_flow_context 真实字段审计

## 核心判断
phase3 难点不是"算 3 因子"，而是 3 个契约是否**同尺度、同时间点、同可回放口径**。
4 大风险：CMF 窗口未冻结 / CMF 与 RS·Flow 尺度不一致 / Flow fail-open 污染总分 / phase2 成分股快照未闭环。

## A. CMF 标准定义（web_search 交叉验证）
- 公式正确
- **StockCharts 默认 20，pandas_ta 默认 length=20，TradingView "通常20或21"，Fidelity 写 21-day**
- 结论：**20 更像主流默认，21 是常见变体**。§24.1 写 CMF(21) 可成立，但应表述为"项目默认21"，非"业界唯一标准"
- high==low → mfm=0（不缩短窗口）
- **Σvolume=0 → 返回 None（非0）**：0=中性，None=未定义，利于下游动态重归一化
- 复权：A股多 qfq，单日 MFM 比例影响小；但 StockDaily 按(code,date) upsert，跨 provider 混写会窗口漂移 → phase3 固定同一日线链路

## B. RS 归一化（关键洞察）
- `raw_rs = stock_return_N - board_return_N`，百分位 `(rank_avg-1)/(n-1)`，n<=1 → 0.5
- N=20
- **⚠️ 契约漂移警示**：若最终只做板块内百分位排名，"减板块指数"对名次**不生效**（同板块同日 board_return_N 是常数）。excess_return 更适合做诊断字段而非排序输入 → §27 C3.2 "语义重要、数学不生效"

## C. Flow 降级
- fail-open → flow_score=None，**不要塞 0.5**（0.5 会把"无数据"伪装成"中性信号"）
- 更稳：**按板块-交易日批次判断 flow_coverage**，低于阈值整批禁用 Flow 再重归一化

## D. 动态重归一化
- 统一尺度：rs∈[0,1]，flow∈[0,1]，**cmf_score=(cmf_raw+1)/2**
- `effective_w_i = base_w_i / Σ(base_w_active)`
- Flow 缺 → RS0.625/CMF0.375
- **RS 或 CMF 缺 → 不算 total，标记 unpriced/degraded**（核心因子）
- ⚠️ **重归一化应按"板块-交易日批次"发生，非随单股**（否则同榜单不同权重，比较性变差）

## E. phase2 依赖（推荐路径）
- 方案2 推荐：current snapshot 跑当日比价，历史 point-in-time 等 phase2 补齐
- **§23.1 vs §25.1 契约漂移**：前者像"历史可无前视回放"，后者承认"仅启用日起积累" → 拆两条能力声明
- **文档写"AlphaSift 成分股链路"，实现是 legulegu**（constituents_snapshot.py:18）→ 契约漂移

## F. 表设计
- PricingSnapshot 最小：id, board_id, stock_code, trade_date, rs_score, cmf(原始[-1,1]), flow_score, total([0,1]), created_at, UNIQUE(board_id,stock_code,trade_date)
- ⚠️ **遗漏**：只存 cmf 不存 cmf_score/factor_mask/run_id → 难追溯 total 权重来源
- 增强：PricingSnapshot 加 factor_mask/run_id；或权重/Flow禁用 全放 PricingFactorRun 反查
- PricingFactorRun：board_id, trade_date, constituent_basis(current|snapshot), rs_window, cmf_window, base_weights_json, effective_weights_json, constituent_count, priced_count, flow_coverage, status, error
- **PricingFactorRun 不做 UNIQUE**（保留多次 rerun 审计）

## G. WBS 3.0-3.8（"冻结口径"导向，先修文档再写码）

## H. §22-§28 内部矛盾与遗漏（关键产出）
1. §23/§24 把 CMF[-1,1] 与 RS/Flow[0,1] 直接加权和 → **尺度不一致**
2. §24.3 旧字段 super_large_net/large_net 与 H-3 真实字段 main_net_inflow 矛盾
3. §23.1 vs §25.1 历史回放承诺矛盾
4. C3.2 板块指数vs均值 在 rank 框架下不生效
5. 漏：RS/CMF 不足N/新股/停牌/零量/全并列 处理
6. 漏：Flow 批次级覆盖阈值
7. 漏：rerun 审计口径（run_id/factor_mask/effective_weights_json）

## 最终建议
- **phase3 P0 = same-day current snapshot + RS/CMF 主链 + Flow best-effort**
- **phase3 P1 = phase2 成分股每日落盘接通后，开放历史 point-in-time 回放**
- 窗口建议统一 20：CMF(20)+RS(20)；若坚持21需写明"项目默认"
- **先修文档收敛口径，再按 WBS 进入实现**

## 外部核验来源
- chartschool.stockcharts.com (CMF)
- tradingview.com CMF
- fidelity.com CMF
- tradingstrategy.ai pandas_ta CMF
