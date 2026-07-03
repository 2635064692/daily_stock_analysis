# Builder 共享上下文 — spi-rotation-phase1

> 所有 Builder 必读。主控已验证的关键范式 + 文件归属锁（一文件一写者）。

## 1. 权威依据
- 需求：`docs/product-specs/spi-rotation-pricing-requirement-boundary.md` §9-§14（阶段1）+ §29（多模型审查修订，**以 §29 为准**）
- 验收：远端容器（CLAUDE.md §5），本机不执行运行/测试命令
- 代码风格：精简高效、无冗余、非必要不写注释文档

## 2. 主控已验证的关键范式（直接用，勿臆测）

### 2.1 建表范式（T3 用）
- `src/storage.py:52` `declarative_base()`，`:68` `Base = declarative_base()`
- `:1161` `Base.metadata.create_all(self._engine)` —— **无 alembic**，沿用此范式

### 2.2 任务队列（T9/T10 用）—— ⚠️ 文档路径有误，以本节为准
- 真实位置：**`src/services/task_queue.py`**（非 §29 所写 `src/core/task_queue.py`）
- `class AnalysisTaskQueue`（`task_queue.py:151`，单例）
- `submit_background_task`（`task_queue.py:465`）
- 状态枚举 `TaskStatus`（`task_queue.py:52`）：`PENDING/PROCESSING/...`（**非** 文档的 pending/running/done）

### 2.3 SPI 时间锚点（T7 用）
- `src/core/trading_calendar.py:194` `def get_effective_trading_date(...)` —— T7 薄封装调用此函数

### 2.4 K 线读源（T8 用）
- `src/repositories/stock_repo.py:57` `StockRepository.get_range(...)` 返回 `StockDaily` 列表
- T8 计算前**先查本地 StockDaily**，miss 才回源 XGB adapter 并落盘（§29 C-2）

### 2.5 路由注册（T10 用）
- `api/v1/router.py:35+` `router.include_router(..., prefix="/xxx")` 范式

### 2.6 定时任务（T11 用）
- `src/scheduler.py:128` `set_daily_task(task, run_immediately)` 范式

## 3. 文件归属锁（⛔ 一文件一写者，禁止越界）

| 任务 | 文件范围 | 类型 |
|------|---------|------|
| T1 | `.ccg/tasks/spi-rotation-phase1/connectivity-report.md` | 调研产出 |
| T2 | `.ccg/tasks/spi-rotation-phase1/ema-golden-sample.md` | 调研产出 |
| T3 | `src/storage.py`（仅追加 2 表类定义） | 改现有 |
| T4 | `src/services/spi/spi_calculator.py`（新）+ `tests/test_spi_calculator.py`（新） | 新建 |
| T5 | `src/services/spi/xuangubao_adapter.py`（新）+ `tests/test_xuangubao_fetcher.py`（新） | 新建 |
| T6 | `src/repositories/plate_spi_repo.py`（新） | 新建 |
| T7 | `src/services/spi/spi_time.py`（新） | 新建 |
| T8 | `src/services/spi/plate_spi_service.py`（新）+ `tests/test_plate_spi_service.py`（新） | 新建 |
| T9 | `src/services/spi/spi_task_runner.py`（新，异步任务编排） | 新建 |
| T10 | `api/v1/endpoints/plate_spi.py`（新）+ `api/v1/router.py`（仅加 include_router 1 行） | 新建+改1行 |
| T11 | `src/scheduler.py`（仅注册 SPI 日终任务 1 处） | 改现有 |

> T3/T10/T11 改现有文件：仅追加，不动现有逻辑。`data_provider/base.py` **绝对不改**（§29 H-1）。
> `src/services/spi/` 目录不存在，首个 Builder（T4/T5/T7）创建时一并建 `__init__.py`。

## 4. 依赖图（主控按此分发）

```
Layer1(p1,并行): T1, T2
Layer2(p2,并行): T3; T4(←T2); T5(←T1); T6(←T3)
Layer3(p3): T7; T8(←T4,T5,T6,T7)
Layer4(p4): T9(←T8); T10(←T9); T11(←T9)
Layer5(p5): T12(←T10,T11)
```
