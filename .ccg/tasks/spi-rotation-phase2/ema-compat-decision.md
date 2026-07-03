# EMA 序列化 BC 兼容决策

## 当前 `ema_for_spi` 调用方

| 文件 | 行号 | 调用方式 | 返回类型依赖 |
|---|---|---|---|
| `src/services/spi/spi_calculator.py` | L33 | `cal_index_spi` 内部调用 | `dict[int, float]`（取 `.values()`） |
| `src/services/spi/plate_spi_service.py` | L11,L43 | 通过 `cal_index_spi` 间接调用 | 不直接使用返回值 |
| `tests/test_spi_calculator.py` | L37,L43,L49,L55,L59,L65,L70,L104,L119,L141,L148 | 直接调用 `ema_for_spi`；断言 `ema_map[p]` 为 float | `dict[int, float]` 强依赖 |

> `spi_task_runner.py` 和 `api/v1/endpoints/plate_spi.py` 均不直接调用 `ema_for_spi` 或 `cal_index_spi`。

## 决策：新增函数，原函数不变

**采用方案：新建 `ema_for_spi_v2`，`ema_for_spi` 原地不动。**

理由：
- 现有测试 11 处直接断言 `ema_map[p]` 为 float，in-place 修改须同步改测试，风险高；
- `cal_index_spi` → `cal_stock_spi` 链路依赖末位 float，改为序列会破坏 v1 SPI 语义；
- 新增函数零侵入，v2 因子模块独立引用，后续可按需合并或废弃。

## 新函数签名

```python
def ema_for_spi_v2(close_series, periods=PERIODS) -> dict[int, list[float]]:
    """返回每个 EMA 周期的完整序列（含 seed 段），供 v2 因子（方向/排列/分离度/压缩）使用。
    Skips period if len(close_series) < period. Seed = SMA(period)，与 ema_for_spi 保持一致。"""
```

- 返回：`{period: [ema_t0, ema_t1, ..., ema_tN]}` — 从第 `period` 根 K 线起的完整 EMA 序列
- 调用方：`spi_factors/` 下 4 个因子模块（阶段2新建，不改现有代码）
- `cal_stock_spi_v2` 接收 `dict[int, list[float]]`，与 `cal_stock_spi` 接口分离
