# AlphaSift `sector_rotation` 运行期数据就绪归档

## 范围

- 成分股 universe fallback：当 AkShare 申万一级板块清单不可用时，回退到最近一次 `plate_spi_snapshot` 板块全集。
- 成分股快照批量补齐：补上 `scripts/sync_sw_constituents.py` 入口，复用 `ShenwanConstituentSyncService` 落盘 `constituent_snapshot`。
- 运行期日线补齐：为 `sector_rotation` 暴露 `daily_history` 开关和并发度，并补充相关测试与文档。

## 验证

- `.venv/bin/python -m py_compile src/repositories/plate_spi_repo.py src/services/spi/rotation_service.py src/services/spi/constituent_sync_service.py src/services/spi/rotation_daily_history_hydrator.py scripts/sync_sw_constituents.py`
- `.venv/bin/python -m pytest tests/test_rotation_service.py tests/test_constituent_sync_service.py tests/test_rotation_daily_history_hydrator.py`
- `.venv/bin/python scripts/check_ai_assets.py`

## 提交

- `3bf846b` `fix(spi)：restore sector rotation runtime data readiness`
