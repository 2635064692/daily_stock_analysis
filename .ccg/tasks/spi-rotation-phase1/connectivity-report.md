# 选股宝（xuangubao）接口连通性与协议报告

> Change ID: spi-rotation-phase1 / 任务 1.1 产出
> 权威依据：Java `b-quant-chan` 源码逐行追溯（`XGBApiGateway` / `AbstractRequest` / 4 EO + 4 Req + Resp 类 + `DefaultHeaderEntity`）
> 用途：阶段1 `xuangubao_adapter.py`（T5）编码的协议蓝本

---

## 0. 协议总览（已确定，无需实测）

| 维度 | 值 | 来源 |
|---|---|---|
| 请求方式 | **GET**，参数走 **query string**（非 path、非 body）| `AbstractRequest.resetUrl` 反射拼 `?field=val&...` |
| 鉴权 | **无**（`DefaultHeaderEntity` 仅加 `Content-Type`）| `DefaultHeaderEntity.createHeader` |
| 成功码 | **`code == 20000`** | `XGBBaseResp.isSuccess` |
| 响应基结构 | `{code, message, data}` | `XGBBaseResp<T>` |
| 域名 | `flash-api.xuangubao.cn`（板块类）、`api-ddc-wscn.xuangubao.cn`（K线） | 4 EO `API` 常量 |
| K线重定向 | kline 接口 `http→https` 有 301 | Codex 之前实测；`requests` 默认跟随 |
| 复权 | 前复权 `forward` | `IXGBStockDataEO.adjustPriceType` |

> ⚠️ **唯一需远端实测**：① DSA 远端容器是否可达；② 真实响应样例；③ 限流上限。见 §6。

---

## 1. 接口①：板块排行 `plate/rank`

获取全量板块 ID 列表（SPI 批量计算的板块清单来源）。

| 项 | 值 |
|---|---|
| **URL** | `http://flash-api.xuangubao.cn/api/plate/rank` |
| 方法 | GET |
| 参数 | `field=core_avg_pcp`（默认）、`type=0`（0=全部/1=概念/2=行业/3=风格）|
| 参数类 | `XGBPlateRankReq{field, type}` |
| 响应 | `XGBPlateRankResp extends XGBBaseResp<List<Integer>>` → `data` = **板块 ID 整数列表** |

**响应样例（待实测）**：
```json
{ "code": 20000, "message": "...", "data": [17014769, 16843401, ...] }
```

---

## 2. 接口②：板块详情+成分股 `plate/plate_set`

SPI 成分股主链路来源（板块→成分股方向）。**注意：返回当前快照成分股，非历史成分股**（见 §7 point-in-time 语义）。

| 项 | 值 |
|---|---|
| **URL** | `http://flash-api.xuangubao.cn/api/plate/plate_set?id={plateId}` |
| 方法 | GET |
| 参数 | `id` = 板块 ID（来自接口①）|
| 参数类 | `XGBPlateDataEO{id}` |
| 响应 | `XGBPlateDataResp.XGBPlateDataVO{name, id, items: List<XGBStockInfoVO>, total_hits}` |

**成分股结构 `XGBStockInfoVO`**：
| 字段 | 含义 | 说明 |
|---|---|---|
| `wscn_code` | **股票代码（SPI 用的 symbol）** | `getSymbol()` 返回此字段，**非 prod_code** |
| `prod_name` | 股票名称 | |
| `market_type` | 市场类型 | |
| `asset_type` | 资产类型 | |

**字段映射（adapter 用）**：`items[].wscn_code → stock_code`、`items[].prod_name → stock_name`

---

## 3. 接口③：个股/批量 K线 `market/kline`

SPI 计算的日线来源。**point-in-time 关键**：`timestamp` 参数控制截至历史日，`backCount` 控制回溯根数。

| 项 | 值 |
|---|---|
| **URL** | `http://api-ddc-wscn.xuangubao.cn/market/kline`（→301→ https）|
| 方法 | GET |
| 参数类 | `XGBStockDataReq` |

**请求参数**（query string，来自 `StockDataTask.getBody` + `IXGBStockDataEO`）：
| 参数名 | 值 | 说明 |
|---|---|---|
| `prod_code` | `"600000.SS,000001.SZ"`（逗号分隔，**批量**）| `isCodeTrans=false` 时传原始符号；Java 每批 5 只（`subList(size-5,size)`）|
| `tick_count` | `"300"`（`backCount`）| 回溯根数，SPI 默认 300 |
| `period_type` | `"day"`（`XuanGuBaoFeign.Freq[DAY]`）| 日频 |
| `timestamp` | 毫秒/1000 = **秒级时间戳** | `calTime.getTime()/1000`，控制 point-in-time 截止日 |
| `adjust_price_type` | `"forward"` | 前复权 |
| `fields` | `tick_at,open_px,close_px,high_px,low_px,turnover_volume,turnover_value,turnover_ratio` | 返回字段 |

**响应结构**（`XuanGuBaoVO` → `XuanGuBaoDataCO`）：
```
{ code:20000, message, data:{
    fields: [tick_at,open_px,close_px,high_px,low_px,turnover_volume,...],
    candle: { "600000.SS": { lines: [[v0,v1,...], ...] } }   // 按 fields 顺序
}}
```
- `data.candle[code].lines` = `List<List<Object>>`，每行按 `fields` 顺序对齐
- 字段映射：`close_px→close`、`open_px→open`、`high_px→high`、`low_px→low`、`turnover_volume→volume`、`tick_at×1000→time(ms)`

**symbol 格式**：`600000.SS`（沪）、`000001.SZ`（深）；DSA 内部 `normalize_code("600000.SS")=="600000"`，adapter 需双向映射。

---

## 4. 接口④：板块历史指数 `plate/index_history`

板块指数历史 K线（比价系统 RS 基准候选 / 板块趋势参考）。

| 项 | 值 |
|---|---|
| **URL** | `https://flash-api.xuangubao.cn/api/plate/index_history` |
| 方法 | GET |
| 参数类 | `XGBPlateHistoryDataReq` |

**请求参数**（来自 `PlateHistoryDataTask.getBody` + `IXGBPlateHistoryDataEO`）：
| 参数名 | 值 | 说明 |
|---|---|---|
| `plate_id` | 板块 ID | |
| `index_type` | `"1"` | |
| `data_count` | `backCount` 字符串 | |
| `end_time` | `endTime.getTime()/1000`（秒级时间戳）| point-in-time 截止 |

**响应结构**（`XGBPlateHistoryDataResp.PlateHistoryItem`）：
```
{ code:20000, data:[{date_time, high, open, low, close}, ...] }
```
- `date_time` 为秒级，`×1000` 转毫秒
- 字段直接是 `high/open/low/close`（无 `_px` 后缀）

---

## 5. 错误处理与超时（复刻 Java 语义）

| 项 | Java 行为 | adapter 复刻 |
|---|---|---|
| 成功判定 | `code != 20000` → 抛 `InfraException` | `resp["code"] != 20000` → 抛异常 |
| HTTP 超时 | `RestTemplate` readTimeout=5000ms / connectTimeout=15000ms（Codex 实测）| `requests` timeout=(15, 5) 或按 D11 并发上限调整 |
| 重定向 | Apache HttpClient 自动跟随 301 | `requests` 默认跟随 |
| 空数据 | `UsefulUtils.isEmpty` 判空 | 成分股空 → 板块 SPI=-1 哨兵（D4）|

---

## 6. 连通性验证（⚠️ 待任务 1.1 远端实测补充）

> 以下为**必须远端实测、无法从协议推导**的部分。占位待填。

### 6.1 DSA 远端可达性
- [ ] 经 SSH MCP 在远端 dsa-src/容器内 curl 4 接口，确认非 Java 宿主独占
- [ ] 是否需经代理（CLAUDE.md 7897）访问 xuangubao.cn

### 6.2 真实响应样例
- [ ] 接口① `plate/rank`：记录真实板块数量、ID 样例
- [ ] 接口② `plate/plate_set?id=<实测ID>`：确认 `items[].wscn_code` 真实格式（是否真为 `600000.SS`）
- [ ] 接口③ `market/kline`：确认 `candle[code].lines` 真实结构 + fields 顺序
- [ ] 接口④ `plate/index_history`：确认 `data[].date_time` 真实单位

### 6.3 限流上限（D11 并发度依据）
- [ ] 实测并发请求触发 429/403 的阈值
- [ ] 据此定 `ThreadPool` 上限（默认 `min(32,cpu*4)`，按实测下调）

---

## 7. 关键设计约束（协议衍生）

1. **point-in-time 语义 = K 线 point-in-time**（§29 C-1/D13）：成分股取 `plate_set` 当前快照，K 线用 `timestamp` 截至历史日。成分股含生存者偏差，与 Java 一致，接受。
2. **symbol 双向映射**：选股宝 `600000.SS` ↔ DSA `600000`，adapter 层用 `normalize_stock_code` 衔接。
3. **批量 K 线每批 5 只**：复刻 Java `subStocks` 的 `subList(size-5,size)` 批次策略，控制单请求数据量。
4. **秒级时间戳**：选股宝用秒（`/1000`），DSA 内部用毫秒/`datetime`，adapter 层转换。
5. **无鉴权 = 公开接口**，但限流风险高（§6.3），回算必须复用 `StockDaily` 本地读源（§29 C-2），仅 miss 才回源。

---

**协议置信度**：High（全部来自 Java 源码逐行追溯，含响应字段名与请求参数名）。
**待实测项**：仅 §6（可达性/样例/限流），不阻塞 T5 编码（可基于本协议先实现，实测后微调）。
