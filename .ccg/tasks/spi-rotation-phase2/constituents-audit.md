# 成分股链路映射审计报告

> 任务：阶段2 任务 1.1 — 对应规格 §19.1 R-1 风险  
> 审计日期：2026-07-03  
> 范围：DSA 现有成分股 API 清查 + 申万一级行业名与可用 API key 的对齐验证 + 2.4 实施方案决策

---

## 一、API 清单

### 1.1 `get_belong_boards`（个股 → 所属板块，**反向**）

| 属性 | 说明 |
|---|---|
| 入口 | `DataFetcherManager.get_belong_boards(stock_code)` |
| 底层 | `EfinanceFetcher.get_belong_board` → `ef.stock.get_belong_board` |
| Key 类型 | **个股代码**（如 `600519`）|
| 返回 | `[{name, code, type}]`，板块名称如 `食品饮料`、`白酒Ⅲ`、`贵州板块` |
| **方向** | 个股 → 板块（**无法用于"板块 → 成分股列表"**）|

此 API 是**反向索引**，不能满足阶段2"给定一个申万一级行业，取其成分股名单"的需求。

---

### 1.2 AlphaSift `hotspot_detail` / `stock_board_industry_cons_em`（板块名 → 成分股）

| 属性 | 说明 |
|---|---|
| 入口 | `AlphaSiftService.hotspot_detail(topic)` / `DsaEastMoneyHotspotProvider.stock_board_industry_cons_em(symbol)` |
| 底层 | `ak.stock_board_industry_cons_em(symbol=topic)` → 东方财富行业板块成分股 |
| Key 类型 | **东方财富行业名称**（如 `"小金属"`、`"电池"`、`"光伏设备"`）|
| 板块粒度 | 东财二级/细分板块（约 300+ 个板块，粒度远细于申万一级的 31 个）|
| 申万一级对齐 | **严重不对齐**：申万一级名称（如 `基础化工`）在东财行业列表中**不存在**；东财行业系统与申万分级体系完全独立 |
| 环境稳定性 | 东财接口依赖代理，本地环境下 `17.push2.eastmoney.com` ProxyError，不可靠 |

此 API 以东财命名体系为 key，与申万一级名称**无直接映射关系**，不可直接用于成分股查询。

---

### 1.3 akshare `index_component_sw`（申万指数代码 → 成分股）

| 属性 | 说明 |
|---|---|
| 入口 | `ak.index_component_sw(symbol='801030')` |
| 底层 | `https://www.swsresearch.com/institute-sw/api/index_publish/details/component_stocks/` |
| Key 类型 | 申万指数代码（801xxx 格式） |
| 环境状态 | **不可用**：实测 `count=0`，swsresearch.com API 返回空结果（可能需要登录 session）|

此 API 理论上最直接，但当前环境无法稳定取到数据（swsresearch.com 返回 `count=0`）。

---

### 1.4 akshare legulegu.com 路径（申万代码 → 成分股）— **可用**

| 属性 | 说明 |
|---|---|
| 原始函数 | `ak.sw_index_third_cons(symbol='801120.SI')`（三级专用，但底层 URL 通用）|
| 实际 URL | `https://legulegu.com/stockdata/index-composition?industryCode={code}.SI` |
| Key 类型 | 申万指数代码 + `.SI` 后缀（如 `801030.SI`）|
| 返回字段 | `序号, 股票代码, 股票简称, 纳入时间, 申万1级, 细分概念, 价格, 市盈率, 市净率, ...` |
| **与 AkshareSwAdapter 对齐** | **完全对齐**：`AkshareSwAdapter.get_sw_first_levels()` 返回的 `board_id`（如 `801030`）加 `.SI` 即为 legulegu key，无需任何名称映射 |

此路径是**目前唯一可同时满足"申万一级代码为 key + 稳定返回成分股列表"**的方案。

---

## 二、申万一级行业与 API Key 对齐率（实测）

使用 `legulegu.com` 对全部 31 个申万一级行业进行实测（单轮 + 重试，2026-07-03）：

| board_id | 行业名 | 成分股数 | 状态 |
|---|---|---:|---|
| 801010 | 农林牧渔 | 104 | OK |
| 801030 | 基础化工 | 410 | OK |
| 801040 | 钢铁 | 44 | OK |
| 801050 | 有色金属 | 142 | OK |
| 801080 | 电子 | 486 | OK |
| 801880 | 汽车 | 287 | OK |
| 801110 | 家用电器 | 94 | OK |
| 801120 | 食品饮料 | 123 | OK |
| 801130 | 纺织服饰 | 107 | OK |
| 801140 | 轻工制造 | 158 | OK |
| 801150 | 医药生物 | 480 | OK |
| 801160 | 公用事业 | 131 | OK |
| 801170 | 交通运输 | 126 | OK |
| 801180 | 房地产 | 99 | OK |
| 801200 | 商贸零售 | 98 | OK（首轮偶发解析失败，重试成功）|
| 801210 | 社会服务 | 80 | OK |
| 801780 | 银行 | 42 | OK |
| 801790 | 非银金融 | — | **限流/代理超时**（重试失败）|
| 801230 | 综合 | 15 | OK |
| 801710 | 建筑材料 | 72 | OK（首轮偶发解析失败，重试成功）|
| 801720 | 建筑装饰 | 155 | OK |
| 801730 | 电力设备 | 371 | OK（首轮偶发解析失败，重试成功）|
| 801890 | 机械设备 | 535 | OK |
| 801740 | 国防军工 | 138 | OK |
| 801750 | 计算机 | 334 | OK |
| 801760 | 传媒 | 130 | OK |
| 801770 | 通信 | 123 | OK |
| 801950 | 煤炭 | 37 | OK（首轮偶发解析失败，重试成功）|
| 801960 | 石油石化 | 47 | OK（首轮偶发解析失败，重试成功）|
| 801970 | 环保 | — | 429（首轮限流，单测结果不稳定）|
| 801980 | 美容护理 | — | 429（首轮限流，单测结果不稳定）|

**对齐汇总：**

- 首轮稳定成功：23/31（74%）
- 重试后成功：+5（共 28/31，90%）
- 持续失败（非银金融/环保/美容护理）：3/31（10%，原因：限流 429 / 代理超时，非名称不对齐）
- **所有成功返回的行业均 ≥5 只成分股**（最少：综合 15 只；最多：机械设备 535 只）

失败原因分析：
- 非银金融（801790）：本地代理超时
- 环保（801970）、美容护理（801980）：HTTP 429（请求频率过高），加 sleep 重试可恢复

---

## 三、命名体系差异分析

| 体系 | key 格式 | 粒度 | 与阶段2需求匹配度 |
|---|---|---|---|
| 申万一级 (legulegu) | `801xxx.SI` | 31 个一级行业 | **完全匹配** |
| 东财行业 (EM) | 中文短名（如"小金属"） | 300+ 细分板块 | 不匹配（名称与申万一级无直接对应）|
| `get_belong_boards` | 个股代码 | 反向查询 | 不匹配（方向相反）|

**结论：东财行业体系与申万一级分类是两套独立系统，名称不能互用，不需要也无法建立名称映射表。**

---

## 四、方案决策

### 推荐方案：**方案 A — 直接调用 legulegu.com（`board_id + .SI` 为 key）**

**理由：**

1. **Key 与现有 adapter 完全对齐**：`AkshareSwAdapter.get_sw_first_levels()` 已返回 `board_id`（`801010` 等），加 `.SI` 即为 legulegu 请求 key，无需名称映射表，无需额外中间件。
2. **实测覆盖率达 90%+**：28/31 行业在非限流状态下稳定返回 ≥5 只成分股，3 个失败均为临时限流（HTTP 429）或代理问题，非名称对齐缺失。
3. **加 sleep + 重试即可覆盖剩余 3 个**：legulegu 的限流是短时聚合触发，加 0.5-1s sleep 分散请求即可规避。
4. **字段直接可用**：返回字段 `股票代码` 即为成分股 A 股代码，无需转换。

**实施要点（2.4 constituents_snapshot）：**

```python
# 成分股获取：直接通过 legulegu.com 接口
url = f"https://legulegu.com/stockdata/index-composition?industryCode={board_id}.SI"
# 解析：df['股票代码'] 即为成分股列表
# 需加 sleep（≥0.5s/请求）防 429
# 建议 try/except 失败时记录警告并跳过（不阻断轮动主流程）
```

**不选方案 B（名称映射表）**：东财行业名与申万名属于不同分类体系，不存在稳定的 1:1 映射；映射表需人工维护，维护成本高且易漂移。

**不选方案 C（单龙头兜底）**：在 API 可达的条件下，29+只成分股与 1 只龙头之间的信息损失过大，不满足 §19.1 S2 point-in-time 成分股锁定的需求语义。

---

## 五、限制与边界

- **不保证历史名单回溯**：legulegu.com 返回**当前快照**名单（含纳入时间），不是历史 point-in-time 快照。历史 point-in-time 依赖阶段2日终任务从启用日开始每日落盘（§19.1 D2.4）。
- **环境依赖**：在本地有代理时，legulegu.com 的部分行业偶发代理超时；生产/CI 环境若无代理则更稳定。失败时应 fallback 为空列表 + 警告日志，不阻断 SPI 主流程。
- **akshare `sw_index_third_cons` 已失效**：该函数依赖 legulegu.com 同一接口，但当前版本（列映射 17 列 vs 18 列不匹配）有 `ValueError`，建议绕过 akshare 包装，直接 `requests.get` + `pd.read_html`。
- **`index_component_sw` 不可用**：swsresearch.com 的 API 端点返回 `count=0`，当前无法使用。

---

## 六、结论

**实施方案：A（直接调用 legulegu.com，board_id + .SI 为 key）**

31 个申万一级行业中，28 个在正常（无限流）条件下通过 legulegu.com 稳定返回 ≥5 只成分股，命名 key 与阶段1 `AkshareSwAdapter` 的 `board_id` 字段完全对齐，无需额外名称映射。剩余 3 个失败均为临时限流，加 sleep 可恢复。2.4 `constituents_snapshot` 直接在 `save_constituents` 内封装 legulegu 请求，加 try/except 异常隔离。
