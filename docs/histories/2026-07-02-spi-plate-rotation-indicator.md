## [2026-07-02 19:10] | Task: 记录 b-quant-chan 后端 SPI 板块轮动指标计算逻辑与数据源

### 🤖 Execution Context

- **Agent ID**: claude-opus-4-8
- **Base Model**: claude-opus-4-8
- **Runtime**: 本机（仅代码分析，无运行/测试命令）

### 📥 User Query

> 获取 /home/github/b-quant-chan 下后端 spi 板块轮动指标的计算方式以及相关逻辑，将 SPI 指标计算逻辑以及所需的数据源信息记录到 history 文档。

### 🛠 Changes Overview

**Scope:** 跨仓库只读分析（b-quant-chan 后端 Java 源码），产出为本仓库 `docs/histories/` 知识记录文档，无代码改动。

**Key Actions:**

- **[追溯计算链路]**: 从消费层 `PlateSPIIndicators` 反向追溯到实体计算层 `PlateSingleIndicatorsEntity.calSPI / calStockSPI`，再到 EMA 簇生成 `Indicators.emaForSPI`，逐层定位 SPI 真实计算口径。
- **[梳理数据源]**: 明确 SPI 所需输入数据 = 板块成分股日线 `SourceBar` 序列（经 `plateElementGateway.subStocks` 按日频取数），落库经 `PlateElementDayValueServiceI`，读侧经 `findMostLimit / findDayValuesLimit`。
- **[固化指标定义]**: 单股 SPI = 收盘价站上 8 条斐波那契周期 EMA 的条数(0~8)；板块 SPI = 成分股 SPI 算术均值(无股则 -1)；时间锚点 = `CommonUtils.spiTime`（≥15:00 取当日 15:00，否则取前一交易日 15:00）。

### 🧠 Design Intent (Why)

b-quant-chan 的 SPI 板块轮动指标逻辑分散在消费层（排序取数）、实体层（板块/单股计算）、工具层（EMA 簇、时间锚点）三处，单看任一文件都无法还原完整口径。本次将全链路计算方式与数据源依赖一次性沉淀到 history 文档，供后续在本仓库（daily_stock_analysis）做对标/复刻/迁移时直接引用，避免重复逆向。

### 📁 Files Modified

- `docs/histories/2026-07-02-spi-plate-rotation-indicator.md`（新建，本文件）

---

## 一、指标定义

SPI（Sector Power Index）= 板块内个股"多头排列强度"的算术平均。用一组斐波那契数列周期的 EMA 构成"多头均线簇"，统计当日收盘价站上多少条 EMA，作为板块强势度量。

- **单股 SPI**：收盘价高于 N 条 EMA 的条数（整数，取值 0 ~ 8）
- **板块 SPI**：板块所有成分股 SPI 的算术平均（`XMathUtil.avg`）；无成分股时取 `-1`（标记节假日/无效）

> 单股 SPI 上界 8（收盘价高于全部 8 条 EMA），下界 0；板块 SPI ∈ [0, 8]，无成分股时为 -1。

## 二、所需数据源

| 数据 | 来源 | 说明 |
| --- | --- | --- |
| 板块 ID 列表 | `xuanGuobaoFeign.plateRank()` | 全量板块，用于批量计算 |
| 板块成分股日线 | `plateElementGateway.subStocks(PlateSubStockQryEO.of(plateId, calTime, FreqType.DAY))` | 返回 `Map<股票, List<SourceBar>>`，日频 K 线 |
| K 线字段 | `SourceBar` | 计算只用 `getClose()` 收盘价序列 |
| 交易日校验 | `SPICheckCurrentDayCmd.checkIsCurrentDay()` | 非交易日跳过计算 |
| 落库 | `PlateElementDayValueServiceI.saveAll()` | 过滤 SPI == -1 后转 PO 入库 |
| 读侧取数 | `findMostLimit(date, 30)` / `findDayValuesLimit(plateIds, 100)` | Top 30 强势板块 × 各 100 日 SPI 序列 |

> **外部依赖**：EMA 实现基于 TA-Lib `core.ema(...)`，对齐到 `size-1` 末位取值。

## 三、核心计算链路

### 1. 触发入口（两类）

| 入口 | 文件 | 作用 |
| --- | --- | --- |
| 定时批量 | `PlateBatchPowerCmd.execute()` | 当日全板块 SPI 计算，提交线程池并发 |
| 历史回补 | `PlateSPIHistoryCmd.execute(Long time)` | 从指定时间向前逐日回算 SPI |

二者均先经 `SPICheckCurrentDayCmd.checkIsCurrentDay()` 校验当日是否为交易日（非交易日跳过），再通过 `xuanGuobaoFeign.plateRank()` 获取全部板块 ID 列表。

### 2. 单板块计算 — `PlateSingleIndicatorsEntity.calSPI(calTime)`

```java
// PlateSingleIndicatorsEntity.java:70
public PlateSingleIndicatorsEntity calSPI(Timestamp calTime){
    Map<String, List<SourceBar>> subStocks = plateElementGateway.subStocks(
        PlateSubStockQryEO.of(plateId, calTime, FreqType.DAY));
    BigDecimal[] SPIs = new BigDecimal[subStocks.size()];
    int i = 0;
    for (Map.Entry<String, List<SourceBar>> entry : subStocks.entrySet()) {
        SPIs[i] = calStockSPI(entry.getValue());   // 逐股计算
        i++;
    }
    BigDecimal spi = SPIs.length == 0 ? new BigDecimal("-1") : XMathUtil.avg(SPIs);
    this.value = PlateElementValueEO.of(calTime, spi);
    return this;
}
```

- 输入：板块成分股的日线 `SourceBar` 序列（按日频取数）
- 输出：`PlateElementValueEO(time=calTime, SPI=板块均值)`

### 3. 单股 SPI — `calStockSPI(List<SourceBar>)`

```java
// PlateSingleIndicatorsEntity.java:87
public static BigDecimal calStockSPI(List<SourceBar> value) {
    Map<Integer,Double> emaSPIMap = INDICATORS.emaForSPI(value);
    SourceBar lastBar = value.get(value.size() - 1);
    int spi = 0;
    double close = lastBar.getClose();
    for (Map.Entry<Integer, Double> entry : emaSPIMap.entrySet()) {
        if (close > entry.getValue()) spi++;   // 收盘价高于该周期 EMA 则计 1
    }
    return new BigDecimal(spi);
}
```

### 4. EMA 簇生成 — `Indicators.emaForSPI()`

```java
// Indicators.java:20
public Map<Integer, Double> emaForSPI(List<SourceBar> sourceBars) {
    ...
    Integer[] maRange = {5, 13, 21, 34, 55, 89, 144, 233};  // 斐波那契数列
    for (Integer integer : maRange) {
        if (size < integer) continue;                         // 数据量不足该周期则跳过
        result.put(integer, ema(closePrices, integer)[size - 1]);  // 取末位 EMA 值
    }
    return result;
}
```

- 均线周期：`{5, 13, 21, 34, 55, 89, 144, 233}`（斐波那契数列，共 8 条）
- 数据不足保护：当 K 线数量 < 周期 时跳过该周期，因此实际参与计数的 EMA 条数随可用历史长度递增（最多 8）
- EMA 实现：基于 TA-Lib 的 `core.ema(...)`，对齐到 `size-1` 末位取值

## 四、时间锚定 — `CommonUtils.spiTime(Date)`

```java
// CommonUtils.java:117
public static Date spiTime(Date date) {
    Date flagTime = UsefulUtils.resetHMS(date, 15, 0, 0);  // 当日 15:00
    return date.compareTo(flagTime) >= 0 ? flagTime : UsefulUtils.before(flagTime, -1);
}
```

- 当 `now ≥ 15:00`（收盘后）→ 用当日 15:00
- 当 `now < 15:00`（盘中/盘前）→ 用前一日 15:00（`before(flagTime, -1)` 即前一交易日收盘点）

即 SPI 锚定到"最近已收盘的交易日"，避免盘中未定型 K 线污染指标。

## 五、数据落库与消费

**落库（写）**：`PlateElementDayValueServiceI.saveAll()` 过滤掉 `SPI == -1`（节假日）记录后，经 `PlateElementDayValue2DomainCover` 转 PO 入库。

**消费（读 — 板块轮动排序）**：`PlateSPIIndicators.calculate()`（`@Order(4)` 指标计算器）：

```java
// PlateSPIIndicators.java:42
Date date = CommonUtils.spiTime(new Date());
List<String> plateIds = plateElementDayValueServiceI.findMostLimit(date, Constant.PLATE_MOST_LIMIT);  // 默认 30
List<PlateElementDayValueEO> dayValueEOS =
    plateElementDayValueServiceI.findDayValuesLimit(plateIds, Constant.PLATE_DAY_VALUES_LIMIT);       // 每板块 100 日
```

- `findMostLimit`：取最近批次全部板块，按 SPI 降序排序，取前 `PLATE_MOST_LIMIT=30` 个 → SPI 最高的 30 个强势板块
- `findDayValuesLimit`：取这 30 个板块各自最近 `PLATE_DAY_VALUES_LIMIT=100` 个交易日的 SPI 序列
- 最终组装为 `List<List<LineEO>>`（每个板块一条 SPI 时序曲线），供前端轮动看板渲染

## 六、关键参数常量（Constant.java）

| 常量 | 值 | 含义 |
| --- | --- | --- |
| `PLATE_MOST_LIMIT` | 30 | 展示/取数的强势板块条数 |
| `PLATE_DAY_VALUES_LIMIT` | 100 | 每板块展示的 SPI 历史天数 |
| EMA 周期簇 | 5,13,21,34,55,89,144,233 | 单股 SPI 计数用的均线周期 |

## 七、逻辑总结

> 对每个板块：取其全部成分股日线 → 每只股算 8 条斐波那契周期 EMA、统计收盘价站上几条得单股 SPI(0~8) → 板块 SPI = 成分股 SPI 均值(无股则 -1) → 按板块 SPI 降序取 Top 30，各取 100 日序列，构成板块轮动强弱时序。

**置信度**：High（逻辑直接来自 b-quant-chan 源码逐行追溯，无外部推测）。

## 八、涉及源码文件清单（b-quant-chan 后端）

- `PlateSPIIndicators.java` — 消费层，板块轮动排序取数（`@Order(4)`）
- `PlateSingleIndicatorsEntity.java` — 实体层，`calSPI` / `calStockSPI` 板块与单股计算
- `Indicators.java` — 工具层，`emaForSPI` 斐波那契 EMA 簇生成
- `CommonUtils.java` — `spiTime` 时间锚点
- `Constant.java` — `PLATE_MOST_LIMIT` / `PLATE_DAY_VALUES_LIMIT` 常量
- `PlateBatchPowerCmd.java` — 定时批量入口
- `PlateSPIHistoryCmd.java` — 历史回补入口
- `SPICheckCurrentDayCmd.java` — 交易日校验
- `PlateElementDayValueServiceI` / `PlateElementDayValue2DomainCover` — 落库与 PO 转换
- `plateElementGateway.subStocks` / `xuanGuobaoFeign.plateRank` — 数据源网关
