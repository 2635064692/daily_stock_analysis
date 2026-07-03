# phase3 比价系统 — 双模型分析综合（复用自 deep-research 规划阶段）

> 复用自 research/{gemini,codex}-analysis.md，避免 full-collaborate Phase 2 重复审计。
> 核心裁定表见规划报告。

## 选定方案（P0）
same-day 板块内比价 = RS(20) + CMF(20) 主链 + Flow best-effort（当日）
- 成分股：legulegu current snapshot（phase2 rotation 懒保存模式已接受）
- 历史回放（P1）待 phase2 显式落盘收敛，phase3 无需改码自动升级

## 关键算法裁定（双模型交叉）
| 议题 | 裁定 | 来源 |
|---|---|---|
| CMF 窗口 | 20（项目默认） | Codex web_search 4源 |
| CMF Σvol=0 | None（非0） | Codex |
| RS | 20日收益板块内百分位，不减指数 | Codex 数学论证 |
| Flow 缺失 | None，批次级 flow_coverage<0.6 整批禁用 | Codex |
| 尺度统一 | cmf_score=(cmf+1)/2 后再加权 | Codex DR4 |
| 重归一化粒度 | 板块-交易日批次 | Codex |
| 表建法 | Base.metadata.create_all（无 alembic） | §29.3 M-6 |

## Gemini 盲点（Codex 已纠）
CMF 业界标准(21vs20)、§24.3 字段漂移、RS 减指数冗余、Alembic 漂移。

## SESSION_ID（Phase 3 architect 复用）
- BACKEND_SESSION: 019f2809-8744-70c2-b4f3-7943715b907e (codex)
- FRONTEND_SESSION: fc0db71d-e326-4c61-a5f7-9621f4ec98cf (gemini)
