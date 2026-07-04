# SPI 阶段3 S/P 比价实施清单

- [x] 1. 扩展 `PricingSnapshot` schema，并补 SQLite 存量列
- [x] 2. 新建 `sp_ratio.py`，实现 S/P 核心算法
- [x] 3. 改造 `pricing_repo.py`，透传 `sp_ratio/sp_score`
- [x] 4. 改造 `pricing_service.py`，切换为 SP 主因子 + CMF/Flow 确认
- [x] 5. 改造 `plate_pricing.py`，暴露 `sp_ratio/sp_score`
- [x] 6. 新增/更新单测覆盖算法、repo、service、API
- [x] 7. 执行 `py_compile` 与目标 pytest 验证
- [x] 8. 同步必要文档与 task 状态
