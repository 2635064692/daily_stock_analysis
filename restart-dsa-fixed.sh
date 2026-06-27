#!/bin/bash
# DSA 服务重启脚本（包含 efinance 权限修复）

set -e

echo "=== 停止并删除旧容器 ==="
docker stop dsa-server 2>/dev/null || true
docker rm dsa-server 2>/dev/null || true

echo ""
echo "=== 启动新容器（仅挂载后端源码 + 权限修复）==="
# 仅挂载后端 Python 源码（落点 /home/haizh/dsa-src，git clone/pull 得到），保留镜像内前端 static/。
# 远端 git pull 拉新分支后，重启即用最新源码逻辑，无需等 CI 重建镜像。
# 镜像依赖位于 /usr/local/lib（site-packages），不受源码挂载影响，无需重新安装（新增 pip 依赖除外）。
# FASTAPI_STARTUP_TIMEOUT=15：挂载源码首次冷 import api.app 较慢，需 > 默认 3s 的余量。
# data/logs/.env 挂载自运行态目录（/home/haizh/opensource/daily_stock_analysis）。
docker run -d \
  --name dsa-server \
  --restart unless-stopped \
  -p 8001:8001 \
  -e FASTAPI_STARTUP_TIMEOUT=15 \
  -v /home/haizh/dsa-src/src:/app/src \
  -v /home/haizh/dsa-src/api:/app/api \
  -v /home/haizh/dsa-src/bot:/app/bot \
  -v /home/haizh/dsa-src/data_provider:/app/data_provider \
  -v /home/haizh/dsa-src/strategies:/app/strategies \
  -v /home/haizh/dsa-src/templates:/app/templates \
  -v /home/haizh/dsa-src/main.py:/app/main.py \
  -v /home/haizh/dsa-src/server.py:/app/server.py \
  -v /home/haizh/dsa-src/webui.py:/app/webui.py \
  -v /home/haizh/opensource/daily_stock_analysis/data:/app/data \
  -v /home/haizh/opensource/daily_stock_analysis/logs:/app/logs \
  -v /home/haizh/opensource/daily_stock_analysis/.env:/app/.env \
  --entrypoint /bin/sh \
  zhulinsen/daily_stock_analysis:latest \
  -c "
    # 修复 efinance 缓存权限
    chmod 666 /usr/local/lib/python3.11/site-packages/efinance/data/search-cache.json 2>/dev/null || true

    # 调用原始 entrypoint
    exec /usr/local/bin/docker-entrypoint.sh python main.py --serve-only --host 0.0.0.0 --port 8001
  "

echo ""
echo "=== 等待容器启动（挂载源码冷启动约需 15s）==="
sleep 20

echo ""
echo "=== 验证容器状态 ==="
docker ps --filter "name=dsa-server" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

echo ""
echo "=== 验证权限修复 ==="
docker exec dsa-server ls -la /usr/local/lib/python3.11/site-packages/efinance/data/search-cache.json

echo ""
echo "✅ 容器启动完成！"
echo "API 地址: http://localhost:8001"
