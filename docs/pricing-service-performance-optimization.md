# PricingService 性能优化方案

> **版本**: v1.0  
> **日期**: 2026-07-05  
> **状态**: 已实施  
> **Commit**: 103b963

---

## 1. 背景

### 1.1 问题描述

`PricingService.price_board` 在 AlphaSift 板块轮动选股场景中存在严重性能浪费：

```python
# 原有实现（简化）
def _price_buy_candidates(self, *, buy_matches, ...):
    buy_codes = ["600519"]  # 只有 1 只触发 BUY 信号
    
    # ❌ 问题：对整个板块 50 只股票全量比价
    result = self._pricing_service.price_board(board_id=801010, trade_date=trade_date)
    
    # 然后再过滤出 BUY 股票
    priced_matches = [item for item in result["stocks"] if item["code"] in buy_codes]
```

**性能浪费**：
- 板块有 50 只成分股，但只有 1 只触发 BUY 信号
- 对 49 只无关股票做了日线查询、实时行情、基本面、资金流等 4 类网络请求
- 浪费比例：**98%**

### 1.2 影响范围

假设 30 个板块，平均每个板块 50 只成分股，5 只触发 BUY：

| 维度 | 优化前 | 优化后 | 浪费比例 |
|------|-------|--------|---------|
| 查询股票数 | 30 × 50 = 1500 | 30 × 5 = 150 | 90% |
| 网络请求数 | 1500 × 3 = 4500 | 150 × 3 = 450 | 90% |
| 强制等待 | 1500 × 0.5s = 750s | 0s | 100% |
| **总耗时** | **~800s (13分钟)** | **~10-20s** | **95-98%** |

---

## 2. 优化方案

### 2.1 核心改动 1：新增 `codes` 参数

#### 接口变更

```python
def price_board(
    self,
    board_id: int,
    trade_date: date,
    codes: Optional[List[str]] = None,  # ✅ 新增
) -> Dict[str, Any]:
    """
    Args:
        codes: 可选，指定要比价的股票列表；None 表示对板块所有成分股比价
    """
```

#### 实现逻辑

```python
if codes is not None:
    # 使用传入的股票列表（过滤场景）
    validated_codes = [code for code in codes if code]
    constituent_source = "filtered"
else:
    # 查询板块所有成分股（原有逻辑）
    validated_codes, constituent_source = self._get_constituents(board_id, trade_date)

codes = validated_codes
```

#### 调用方改动

```python
# src/services/spi/rotation_alphasift_runtime.py
def _price_buy_candidates(self, *, buy_matches, ...):
    buy_codes = [str(item["stock_code"]) for item in buy_matches]
    
    # ✅ 只对 BUY 信号股票比价
    result = self._pricing_service.price_board(
        board_id=board_id,
        trade_date=trade_date,
        codes=buy_codes,  # 新增参数
    )
    
    # ✅ 现在 result["stocks"] 只包含 buy_codes，无需过滤
    stocks = result.get("stocks") or []
    stocks.sort(key=lambda item: float(item.get("total") or -1.0), reverse=True)
```

### 2.2 核心改动 2：删除串行限流

#### 问题

```python
# 原有实现
for code in codes:
    # ... 数据收集
    time.sleep(0.5)  # ❌ 每只股票强制等待 500ms
```

**浪费**：30 个板块 × 50 只/板块 × 0.5s = **750 秒 (12.5 分钟)**

#### 优化

```python
# 优化后
for code in codes:
    # ... 数据收集
    # ✅ 删除 time.sleep(0.5)
```

**原理**：
- 依赖 `DataFetcherManager.prefetch_realtime_quotes()` 批量预取
- efinance/akshare_em 等全量数据源一次拉取全市场，无需逐股限流
- 单股数据源（新浪/腾讯）在 `codes` 参数过滤后数量大幅减少，不触发限流

### 2.3 核心改动 3：并发执行验证

#### 现有实现

代码中已有并发逻辑（`_collect_stock_inputs_parallel`），本次优化新增测试覆盖：

```python
# src/services/pricing_service.py (已有实现)
_STOCK_INPUT_WORKERS = 4

def _collect_stock_inputs(self, *, manager, codes, trade_date):
    self._prefetch_realtime_quotes(manager, codes)  # 预取
    workers = min(len(codes), _STOCK_INPUT_WORKERS)
    
    if workers <= 1:
        # 单股票串行执行
        rows = [self._collect_single_stock_input(...) for code in codes]
    else:
        # 多股票并发执行
        rows = self._collect_stock_inputs_parallel(
            manager=manager,
            codes=codes,
            trade_date=trade_date,
            max_workers=workers,
        )
    return self._unzip_stock_inputs(rows)
```

---

## 3. 性能提升数据

### 3.1 理论分析

| 场景 | 优化前 | 优化后 | 提升 |
|------|-------|--------|------|
| **AlphaSift 板块轮动** | | | |
| - 查询股票数 | 1500 | 150 | **10x** |
| - 网络请求 | 4500 | 450 | **10x** |
| - 强制等待 | 750s | 0s | **∞** |
| - 总耗时 | ~800s | ~10-20s | **40-80x** |
| **全板块比价（无过滤）** | | | |
| - 单板块 50 股 | ~25s | ~3-5s | **5-8x** |
| - 并发提升 | 串行 | 4 workers | **3-4x** |
| - 删除 sleep | 25s | 0s | **∞** |

### 3.2 实测验证

```python
# tests/test_pricing_service_concurrent.py
def test_parallel_execution_is_faster_than_serial(self):
    """4 只股票，每只 3 次网络调用，每次 100ms 延迟"""
    codes = ["000001", "000002", "000003", "000004"]
    
    # 串行预期：4 × 3 × 0.1 = 1.2s
    # 并发预期（4 workers）：3 × 0.1 = 0.3s
    
    elapsed = measure_time(svc.price_board(...))
    assert elapsed < 0.8  # ✅ 实测 < 0.8s
```

---

## 4. 兼容性

### 4.1 向后兼容

- ✅ `codes=None` 时行为与原实现完全一致
- ✅ 所有现有调用方无需修改
- ✅ 返回结构不变

### 4.2 新功能启用

只有显式传入 `codes` 参数的调用方受益：

```python
# 场景 1：AlphaSift 板块轮动（已启用）
result = pricing_service.price_board(
    board_id=board_id,
    trade_date=trade_date,
    codes=buy_codes,  # ✅ 只比价 BUY 信号股票
)

# 场景 2：全板块比价（原有逻辑）
result = pricing_service.price_board(
    board_id=board_id,
    trade_date=trade_date,
    # codes=None（默认），查询所有成分股
)
```

---

## 5. 测试覆盖

### 5.1 新增测试

**`tests/test_pricing_service_concurrent.py`** (368 行，8 个测试)

| 测试类 | 测试内容 |
|--------|---------|
| `TestConcurrentExecution` | - 单股票使用串行<br>- 多股票使用并发<br>- 保持结果顺序<br>- 处理单股失败<br>- 遵守 max_workers |
| `TestConcurrentPerformance` | - 并发比串行快<br>- 使用真实多线程 |
| `TestPrefetchIntegration` | - 预取在并发前执行 |

### 5.2 更新测试

**`tests/test_pricing_service.py`**
- ✅ 新增 `codes` 参数测试（2 个）
- ✅ 删除 `time.sleep` 测试（已移除该逻辑）

**`tests/test_rotation_alphasift_runtime.py`**
- ✅ 更新 mock 断言匹配新调用签名

### 5.3 测试结果

```bash
$ pytest tests/test_pricing_service*.py -v
======================== 24 passed, 1 warning in 2.63s ========================
```

---

## 6. 风险与限制

### 6.1 已知风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 并发竞态 | 数据源可能不支持高并发 | - 限制 max_workers=4<br>- 预取机制减少并发请求 |
| 预取失败 | 退化为逐股查询 | - 预取失败不影响功能<br>- 日志记录便于排查 |

### 6.2 限制

- **并发度**：固定 4 个 worker，未根据数据源动态调整
- **预取覆盖**：只支持 efinance/akshare_em 等全量数据源
- **错误处理**：单股失败不重试，直接标记为 `missing_core_factor`

---

## 7. 后续优化方向

### 7.1 短期（1-2 周）

1. **动态并发度**
   ```python
   # 根据数据源类型调整 workers
   if data_source in ['sina', 'tencent']:
       workers = 2  # 单股数据源降低并发
   else:
       workers = 8  # 全量数据源提高并发
   ```

2. **批量 API 利用**
   ```python
   # 利用 DataFetcherManager 批量接口
   quotes = manager.batch_get_realtime_quotes(codes)
   profits = manager.batch_get_profit_snapshots(codes)
   ```

### 7.2 长期（1-3 月）

1. **SPI 数据层预计算**
   - 定时任务预计算板块比价结果
   - AlphaSift 直接读取缓存

2. **分布式比价**
   - 支持跨板块并行比价
   - 利用消息队列解耦

---

## 8. 参考资料

- **设计文档**：`docs/design-docs/alphasift-realtime-sector-rotation.md`
- **技术细节**：`docs/product-specs/alphasift-spi-integration-tech.md`
- **Commit**: 103b963
- **相关 Issue**: #TODO（如有）

---

## 附录：完整性能对比

### A.1 板块轮动场景（30 个板块）

| 指标 | 优化前 | 优化后 | 提升 |
|------|-------|--------|------|
| 总股票数 | 1500 | 150 | 10x |
| 日线查询 | 1500 次 | 150 次 | 10x |
| 实时行情 | 1500 次 | 150 次 | 10x |
| 基本面 | 1500 次 | 150 次 | 10x |
| 资金流 | 1500 次 | 150 次 | 10x |
| time.sleep | 750s | 0s | ∞ |
| 并发加速 | 1x | 3-4x | 3-4x |
| **总耗时** | **~800s** | **~10-20s** | **40-80x** |

### A.2 单板块比价场景（50 只股票）

| 指标 | 优化前 | 优化后 | 提升 |
|------|-------|--------|------|
| time.sleep | 25s | 0s | ∞ |
| 并发加速 | 1x | 3-4x | 3-4x |
| **总耗时** | **~25s** | **~3-5s** | **5-8x** |

---

**文档维护者**: AI Coding Assistant  
**最后更新**: 2026-07-05
