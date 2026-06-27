#!/bin/bash
# DSA 服务重启脚本（整体挂载源码 + efinance 权限修复）
#
# 前置条件：
#   1. /home/haizh/dsa-src 是已完成 git clone/pull 的源码 checkout
#   2. 首次启动或前端有改动时，先 build 前端：
#      source ~/.nvm/nvm.sh && cd /home/haizh/dsa-src/apps/dsa-web && npm ci && npm run build
#      （产物自动输出到 /home/haizh/dsa-src/static/，随整体挂载进入容器）
#   3. 纯后端改动只需 git pull 后重跑本脚本，无需重新 build 前端

set -e

echo "=== 停止并删除旧容器 ==="
docker stop dsa-server 2>/dev/null || true
docker rm dsa-server 2>/dev/null || true

echo ""
echo "=== 启动新容器（整体挂载源码到 /app + 权限修复）==="
# 整体挂载 /home/haizh/dsa-src:/app，git pull 后重启即用最新代码，无需等 CI 重建镜像。
# data/logs/.env 子路径挂载覆盖整体挂载中的对应目录，指向运行态目录。
# 镜像依赖位于 /usr/local/lib（site-packages），不受 /app 挂载影响。
# FASTAPI_STARTUP_TIMEOUT 代码默认已是 15s（挂载源码冷启动约需 9s），无需显式传入。
docker run -d \
  --name dsa-server \
  --restart unless-stopped \
  -p 8001:8001 \
  -v /home/haizh/dsa-src:/app \
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
