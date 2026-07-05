# AlphaSift 实时板块轮动选股设计方案

- 当前状态：proposal
- 适用范围：AlphaSift `sector_rotation` 策略、SPI 轮动链路、运行流展示
- 关联文档：`docs/alphasift-integration.md`

## 1. 背景

当前 AlphaSift 选股页已经暴露了 `sector_rotation` 策略入口，但该策略在 DSA 侧的现有实现更接近**读模型**：

- 读取已有 `SpiRotationSignal`
- 读取已有 `PricingSnapshot`
- 进行板块/个股排序后输出候选

这意味着：

- 选股结果依赖前置数据任务是否提前完成；
- AlphaSift 任务本身并没有完整执行 `top v2 板块池 -> 成分股 -> 个股回踩 BUY -> BUY 个股做比价 -> 输出候选股票`；
- 前端运行流里也无法准确反映每一步的实时执行状态与节点详情。

本设计文档要解决的是：将该策略升级为**一次 AlphaSift 后台任务内部的实时编排式选股流程**，同时复用现有首页任务面板和 RunFlowPanel 展示完整工作流。

## 2. 目标与非目标

### 2.1 目标

将当前“读已有 BUY 信号表 / 比价快照表后输出候选”的 `sector_rotation`，升级为一次 AlphaSift 任务内部**实时执行**的策略编排链，并让首页 / 选股页可查看每个节点详情。

本方案要求选股流程严格按以下五步执行，而不是直接查最终结果表：

1. `top v2 板块池`
2. `成分股`
3. `个股回踩 BUY`
4. `BUY 个股做比价`
5. `输出候选股票`

`PlateSpiSnapshot / SpiRotationSignal / PricingSnapshot` 仍可继续写入，作为缓存、审计和复用产物，但**不再作为本次 AlphaSift 选股的前置依赖**。

### 2.2 非目标

本次设计**不包含**以下范围：

- 不把整套 SPI / 比价逻辑迁移到外部 `alphasift` 仓库中做原生实现；
- 不重写现有前端 RunFlow 图组件；
- 不在本阶段引入新的持久化运行流表结构；
- 不改变当前普通 AlphaSift 策略通过 `alphasift.dsa_adapter.screen(...)` 执行的主路径；
- 不要求删除已有 `SpiRotationSignal / PricingSnapshot / PlateSpiSnapshot` 的写库行为。

## 3. 现状问题

当前实现的主要问题：

1. **执行语义不完整**
   - `sector_rotation` 任务本身没有实时跑完五步选股流程，更多是在消费已有表数据。

2. **前置依赖过强**
   - 如果 BUY 信号或 pricing snapshot 未提前准备，任务结果会退化或直接为空。

3. **运行流可见性不足**
   - 首页能看到 AlphaSift 任务，但无法清楚分辨“板块池、成分股、BUY 判断、比价、候选输出”这些业务节点。

4. **策略入口和策略执行不对齐**
   - 用户从 AlphaSift 页面触发的是“执行一次选股任务”的心智模型，但当前 `sector_rotation` 更像“读取既有结果快照”。

## 4. 约束与设计原则

- 保持 `AlphaSiftService.screen(strategy="sector_rotation")` 这一入口不变，避免前端 API 再次分叉。
- 继续复用 `TaskQueue`、`useTaskStream`、`RunFlowPanel`，不新增并行任务系统。
- 继续允许将中间结果写入数据库，但数据库是**副产物**，不是本次任务执行的必要前提。
- 尽量复用现有 SPI 代码：`PlateSpiService`、`RotationService`、`PricingService`、`ConstituentRuntimeResolver`。
- 运行流 metadata 只放低敏感、可前端直接展示的摘要字段，不写入大体量原始响应。

## 5. 推荐后端结构

- 保留 `api/v1/endpoints/alphasift.py -> /screen/tasks` 不变，继续把任务放入统一 `TaskQueue`。
- 在 `src/services/alphasift_service.py` 中保留 `strategy == "sector_rotation"` 的策略入口，但把当前 `_screen_sector_rotation()` 从“读表聚合器”改为“实时编排器”调用。
- 新增一个面向 AlphaSift 的实时策略服务，例如：
  - `src/services/spi/rotation_alphasift_runtime.py`
  - 或 `src/services/alphasift_sector_rotation_service.py`
- 该服务只负责一次任务内的实时执行与运行流打点；现有 `RotationService`、`PricingService`、`PlateSpiService`、`ConstituentRuntimeResolver` 等作为底层能力被复用。

建议接口形态：

```python
class AlphaSiftSectorRotationRuntime:
    def run(self, *, trade_date: date, max_results: int) -> dict:
        ...
```

### 5.1 角色划分

- `AlphaSiftService`
  - 负责 AlphaSift 策略入口、参数校验、统一返回结构、DSA enrich 收口。
- `AlphaSiftSectorRotationRuntime`
  - 负责实时执行五步业务链、汇总 warnings、发出运行流节点事件。
- SPI 底层服务
  - `PlateSpiService`：实时计算 / 刷新 v2 板块分；
  - `RotationService`：成分股解析、回踩 BUY 判断；
  - `PricingService`：板块内比价计算。

## 6. 五步实时执行链

### 6.1 Top v2 板块池

- 目标：在任务内实时得到 `watchpool_top_n` 个 v2 板块观察池。
- 复用能力：
  - `src/services/spi/plate_spi_service.py -> refresh_all_v2() / compute_board_spi_v2()`
  - `src/repositories/plate_spi_repo.py -> find_top_boards_v2()`
- 设计要求：
  - 先执行一次 `refresh_all_v2(trade_date)`（全量或按需），再读取 top N 板块；
  - `top_n` 默认读取 `strategies/rotation_entry.yaml` 中的 `watchpool.top_n`（当前默认 30）；
  - 返回板块时保留 `board_id / board_name / v2_score` 供后续节点与前端详情面板展示。

### 6.2 成分股

- 目标：对每个观察池板块实时解析成分股，优先快照、必要时实时抓取补齐。
- 复用能力：
  - `RotationService._get_snapshot_constituents()`
  - `ConstituentRuntimeResolver / ConstituentSnapshotRepo / ConstituentFetcher`
- 设计要求：
  - 将“解析板块成分股”沉淀为可直接复用的方法，返回：
    - `codes`
    - `source`（`snapshot/current/stale/missing`）
    - `snapshot_meta`
  - 前端节点详情至少展示：
    - `board_id`
    - `board_name`
    - `constituent_count`
    - `source`
    - `snapshot_age_days`（如有）

### 6.3 个股回踩 BUY

- 目标：对观察池成分股实时做“回踩 EMA20 + 量比达标”的 BUY 判断。
- 复用能力：
  - `src/services/spi/rotation_service.py -> check_entry()`
- 设计要求：
  - 将现有 `check_entry()` 拆成：
    - **纯计算函数**：返回本次任务内的 BUY 结果
    - **可选持久化**：如需写 `SpiRotationSignal`，作为副产物执行
  - 不应要求“任务开始前数据库里已经存在 BUY 信号”；
  - 前端节点详情至少展示：
    - `board_id`
    - `board_name`
    - `scanned_count`
    - `buy_count`
    - `ema_period`
    - `volume_ratio_threshold`
    - `pullback_tolerance`

### 6.4 BUY 个股做比价

- 目标：仅对本次实时筛出的 BUY 股票所属板块实时计算比价，不直接依赖历史 `PricingSnapshot` 作为前置输入。
- 复用能力：
  - `src/services/pricing_service.py -> price_board(board_id, trade_date)`
- 设计要求：
  - 以 `price_board()` 为主执行路径，它会实时计算板块内个股的 `sp/cmf/flow/total`，同时可继续落库到 `PricingSnapshot`；
  - 本次策略只保留 `stock_code in buy_signal_codes` 的比价结果参与后续排序；
  - 当前 `find_board_pricing_rank()` 只应作为回放/审计/兜底读取入口，不应再是本次 AlphaSift 实时策略的核心主路径；
  - 前端节点详情至少展示：
    - `board_id`
    - `board_name`
    - `buy_count`
    - `priced_count`
    - `run_id`
    - `flow_coverage`
    - `status`

### 6.5 输出候选股票

- 目标：聚合所有板块的 BUY 股票并输出最终候选。
- 排序规则维持当前语义：
  - 先按 `board_v2_score`
  - 再按个股 `total`
- 设计要求：
  - 允许保留无比价数据的 BUY 股票，但应标注为 `missing_pricing` 或同类状态；
  - 输出 payload 继续兼容 AlphaSift 页面当前字段（`candidates / candidate_count / warnings / rotation_boards` 等）；
  - 前端节点详情至少展示：
    - `rotation_boards`
    - `candidate_count`
    - `max_results`
    - `board_rank_rule`
    - `stock_rank_rule`

## 7. 运行流节点设计

为了让首页与选股页能查看每个节点详情，建议这条实时策略显式发出阶段节点事件。推荐最少节点如下：

- `rotation_watchpool`
  - 标题：`Top v2 板块池`
  - lane：`analysis`
- `rotation_constituents_{board_id}`
  - 标题：`解析成分股 · {board_name}`
  - lane：`data_source`
- `rotation_entry_{board_id}`
  - 标题：`回踩 BUY 扫描 · {board_name}`
  - lane：`analysis`
- `rotation_pricing_{board_id}`
  - 标题：`板块内比价 · {board_name}`
  - lane：`analysis`
- `rotation_candidates`
  - 标题：`输出候选股票`
  - lane：`artifact`
- `rotation_dsa_enrich`
  - 标题：`DSA 增强`
  - lane：`artifact`

每个节点建议至少发两类事件：

- `*_started`
- `*_completed`

失败或降级时发：

- `*_failed`
- `*_fallback`

### 7.1 节点状态语义

- `running`：步骤正在执行；
- `success`：步骤完成且结果可用；
- `degraded` / `fallback`：步骤完成但有降级或兜底；
- `failed`：步骤失败并导致任务整体失败，或该板块被跳过；
- `skipped`：步骤因为前置条件不满足而未运行（例如无 BUY 股票时跳过比价）。

## 8. 节点详情元数据约定

当前前端 `RunFlowNodeDetails` 与 `RunFlowEventList` 会直接展示节点 / 事件 metadata（除少数隐藏键外），因此 metadata 应尽量使用稳定、低敏感、短文本字段。推荐统一使用 camelCase，至少包含以下键：

- watchpool 节点：
  - `tradeDate`
  - `topN`
  - `boardCount`
  - `boardIds`
- constituents 节点：
  - `boardId`
  - `boardName`
  - `constituentCount`
  - `source`
  - `snapshotAgeDays`
- entry 节点：
  - `boardId`
  - `boardName`
  - `scannedCount`
  - `buyCount`
  - `emaPeriod`
  - `volumeRatioThreshold`
  - `pullbackTolerance`
- pricing 节点：
  - `boardId`
  - `boardName`
  - `buyCount`
  - `pricedCount`
  - `runId`
  - `flowCoverage`
  - `status`
- candidates 节点：
  - `rotationBoards`
  - `candidateCount`
  - `maxResults`
  - `sortKeys`

如果需要在节点详情中展示股票列表，建议限制为短列表：

- `buyCodesPreview`
- `candidateCodesPreview`

避免把全部成分股、全部原始 bars、完整行情响应直接塞进 metadata。

## 9. 前端展示方案

### 9.1 首页

- 不新增 API；
- 继续复用 `TaskPanel + RunFlowPanel + /api/v1/analysis/tasks/{task_id}/flow`；
- 只要后端任务在 `TaskQueue` 中运行并发出上述节点事件，首页就可以直接看到这条 AlphaSift 选股工作流。

### 9.2 选股页

- 保留当前 `sessionStorage + /api/v1/alphasift/screen/tasks/{task_id}` 结果轮询；
- 建议额外增加“查看工作流”按钮或嵌入式抽屉，直接复用：

```tsx
<RunFlowPanel source={{ type: 'task', taskId: activeTaskId }} />
```

### 9.3 节点详情

- `RunFlowNodeDetails` 已支持展示 `provider / duration / attempts / recordCount / metadata`；
- `RunFlowEventList` 已支持展示事件级 metadata；
- 因此本次改造的关键不在前端图组件，而在后端节点 metadata 设计是否足够清晰、稳定、可读。

## 10. 接口与返回结构影响

- AlphaSift 现有接口保持不变：
  - `POST /api/v1/alphasift/screen/tasks`
  - `GET /api/v1/alphasift/screen/tasks/{task_id}`
- 运行流读取接口保持不变：
  - `GET /api/v1/analysis/tasks/{task_id}/flow`
- `sector_rotation` 最终返回结构应继续兼容当前前端字段：
  - `enabled`
  - `candidates`
  - `candidate_count`
  - `strategy`
  - `market`
  - `warnings`
  - `rotation_boards`
  - `dsa_enrichment`

新增的运行流信息不通过 AlphaSift screen result 直接扩字段暴露，而通过现有 `TaskQueue.flow_events` 与 run-flow snapshot 侧通道展示。

## 11. 实施顺序

1. 新增实时编排器，先把 `sector_rotation` 的执行链从“读库聚合”改成“任务内实时执行”。
2. 给五步链路补齐 `flow_events`，确保首页 `RunFlowPanel` 可以看到完整节点。
3. 选股页增加“查看工作流”入口，复用现有 `RunFlowPanel`。
4. 保留现有数据库写入，但把它降级为缓存 / 审计副产物，而不是任务前置条件。

## 12. 验收标准

实现完成后，应至少满足以下验收标准：

1. 从 AlphaSift 选股页触发 `sector_rotation` 时，不依赖预先存在的 BUY 信号表和比价快照表即可产出候选或明确失败原因。
2. 首页任务面板能够看到该 AlphaSift 任务，并可打开完整运行流。
3. 运行流中至少能看到：
   - Top v2 板块池
   - 成分股解析
   - 回踩 BUY 扫描
   - 板块内比价
   - 输出候选股票
4. 每个节点详情面板至少显示本设计文档约定的关键 metadata。
5. `sector_rotation` 返回结果仍兼容现有 AlphaSift 前端页面，不要求额外 schema 迁移。

## 13. 风险与回滚

### 13.1 主要风险

- 实时执行链会增加单次 AlphaSift 任务耗时；
- 板块数较多时，成分股解析、BUY 扫描和比价可能成为明显耗时热点；
- 如果节点事件过多或 metadata 过大，可能导致运行流噪音过高、SSE 负载上升；
- 将原“查库读模型”改为“实时执行模型”后，需要重新审视失败与降级文案。

### 13.2 回滚策略

- 业务回滚：
  - 保留当前查库型 `SectorRotationScreener` 实现作为 fallback 路径；
  - 通过服务端开关或代码分支切回原 `_screen_sector_rotation()` 实现。
- 展示回滚：
  - 即使运行流节点打点不完整，任务仍可继续通过原有 `progress/message` 维持基本可用。

## 14. 开放问题

- 是否需要为“每个板块的比价执行”增加并发控制与超时策略？
- 是否需要将选股页也默认内嵌工作流面板，而不是仅从首页进入？
- 是否需要在任务完成后把运行流摘要持久化，支持历史回看？

## 15. 与当前实现的差异

- **当前**：`sector_rotation` 更接近“读已有 BUY 信号表 + 比价快照表后排序输出”。
- **目标**：`sector_rotation` 变成“在一次 AlphaSift 任务内实时跑完 top v2 板块池 -> 成分股 -> 个股回踩 BUY -> BUY 个股做比价 -> 输出候选股票，并同步暴露运行流节点详情”。
