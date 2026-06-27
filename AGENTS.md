# AGENTS.md

本文件说明本仓库的开发验收环境、项目架构与对应命令。

> **核心约束：本机不执行任何命令，仅做代码分析与修改。所有验收测试都在远端进行。**

## 1. 开发模式与角色边界

- **本机**：仅做代码检索、分析、修改、Git 提交推送。**不执行任何运行/测试/构建命令**（本机 `.venv` 缺 `efinance` 等运行依赖，跑不了分析主流程，也不在本机验收）。
- **远端宿主机**（SSH MCP，connection `default`，host `192.168.201.148`）：源码 `git clone`/`pull` 的落点，所有验收命令经此执行。
- **远端容器**：源码挂载进容器后，在容器内跑 `py_compile` / `pytest` / `ci_gate`；服务运行与接口验证在 `dsa-server` 容器。

## 2. 远端开发环境（SSH / Docker）

- 所有远端命令通过 SSH MCP 工具 `mcp__mcp-router__execute-command` 执行（connection name: `default`），不在远端手工跑，不做写入性操作（Git 写操作除外，见 §3）。
- SSH MCP 执行建议：一次批次不超过 **5 条命令**，分批执行。
- 远端源码落点：宿主机 `/home/haizh/dsa-src`（git clone 得到，见 §5.1）。
- 服务运行容器：`dsa-server`，镜像 `zhulinsen/daily_stock_analysis:latest`，对外端口 `8001`，容器内工作目录 `/app`，运行命令 `python main.py --serve-only --host 0.0.0.0 --port 8001`。
- **远端访问 github.com 必须经代理 `http://127.0.0.1:7897`**（直连不通）；`git clone`/`pull` 需带 `-c http.proxy=http://127.0.0.1:7897`。
- `dsa-server` 是 Docker Hub 发布镜像、**仅挂载后端 Python 源码**（`src/`、`api/`、`main.py` 等，落点 `/home/haizh/dsa-src`），**保留镜像内前端 `static/`**；远端 `git pull` 拉新分支后重启即用最新源码逻辑；依赖位于镜像 `/usr/local/lib`（site-packages），不受源码挂载影响（冷启动需 `FASTAPI_STARTUP_TIMEOUT` 默认 15s，见 §5.3）。镜像内仍**默认无 `tests/`、`scripts/`、`pytest`、`flake8`**（随源码挂载进入，但宿主机/临时挂载容器才是单测/静态检查的执行位置，见 §5.2）。

## 3. Git 约束（强制）

- 远端宿主机**仅允许 `git pull`**（带 7897 代理）。
- 严禁在远端执行 `git push`、`git commit` 或任何写入性 Git 操作。
- 本机允许 `git commit`、`git push`（本机是唯一写入点）。
- 链路：本机改代码 → 本机 `git push` → 远端 `git pull`（7897 代理）→ 挂载容器内验收。

## 4. 项目架构

- 项目定位：股票智能分析系统，覆盖 A 股、港股、美股。
- 主流程：抓取数据 -> 技术分析/新闻检索 -> LLM 分析 -> 生成报告 -> 通知推送。
- 关键入口：
  - `main.py`：分析任务主入口
  - `server.py`：FastAPI 服务入口
  - `apps/dsa-web/`：Web 前端
  - `apps/dsa-desktop/`：Electron 桌面端
- 核心职责：
  - `src/core/`：主流程编排
  - `src/services/`：业务服务层
  - `src/repositories/`：数据访问层
  - `src/reports/`：报告生成
  - `src/schemas/`：Schema / 数据结构
  - `data_provider/`：多数据源适配与 fallback
  - `api/`：FastAPI API
  - `bot/`：机器人接入
  - `scripts/`：本地脚本（含 `ci_gate.sh`）
  - `tests/`：pytest 测试（198 个）

## 5. 验收命令（全部在远端执行）

### 5.1 远端拉取最新源码（经 SSH MCP）

```bash
# 首次 clone（带 7897 代理）
git -c http.proxy=http://127.0.0.1:7897 -c https.proxy=http://127.0.0.1:7897 \
  clone https://github.com/2635064692/daily_stock_analysis /home/haizh/dsa-src

# 后续更新（本机 push 后，远端 pull）
cd /home/haizh/dsa-src && \
  git -c http.proxy=http://127.0.0.1:7897 -c https.proxy=http://127.0.0.1:7897 pull --ff-only
```

### 5.2 挂载源码进容器做代码验收（py_compile / pytest / ci_gate）

dsa-server 也挂载 `/home/haizh/dsa-src:/app`（同 §5.3），但代码验收用**独立的临时挂载容器**（`docker run --rm`，跑完即销毁），便于补装 pytest/flake8（`requirements.txt` 不含这两个）而不污染长驻服务容器：

```bash
# 进入挂载源码的容器跑 ci_gate 全流程（syntax / flake8 / deterministic / offline-tests）
docker run --rm -v /home/haizh/dsa-src:/app -w /app \
  zhulinsen/daily_stock_analysis:latest \
  bash -lc "pip install -q flake8 pytest && ./scripts/ci_gate.sh"

# 仅语法检查（最快，无需装 pytest）
docker run --rm -v /home/haizh/dsa-src:/app -w /app \
  zhulinsen/daily_stock_analysis:latest \
  bash -lc "./scripts/ci_gate.sh syntax"

# 仅离线单测
docker run --rm -v /home/haizh/dsa-src:/app -w /app \
  zhulinsen/daily_stock_analysis:latest \
  bash -lc "pip install -q pytest && python -m pytest -m 'not network'"
```

> 支持的 ci_gate 阶段：`all` / `syntax` / `flake8` / `deterministic` / `offline-tests`。

### 5.3 dsa-server 服务验收（运行 / 接口验证）

dsa-server **仅挂载后端 Python 源码**（`src/`、`api/`、`bot/`、`data_provider/`、`strategies/`、`templates/`、`main.py`、`server.py`、`webui.py`，落点 `/home/haizh/dsa-src`），**保留镜像内前端 `static/`**；跑的是挂载源码而非镜像内代码。远端 `git pull`（§5.1，7897 代理）拉新分支后，重启 dsa-server 即用最新源码逻辑，**无需等 CI 重建镜像**。镜像依赖位于 `/usr/local/lib`（site-packages），不受源码挂载影响；仅当新代码引入新的 pip 依赖时，才需 `docker pull` 新镜像。

> ⚠️ **只挂后端源码、不要整体挂 `/app`**：整体 `-v /home/haizh/dsa-src:/app` 会丢失镜像内预构建的前端 `static/`，导致 FastAPI 启动加载前端资源失败。

```bash
# 更新源码并重启（先 git pull 拉新分支，再重启挂载后端源码的 dsa-server）
cd /home/haizh/dsa-src && \
  git -c http.proxy=http://127.0.0.1:7897 -c https.proxy=http://127.0.0.1:7897 pull --ff-only
docker stop dsa-server && docker rm dsa-server
docker run -d \
  --name dsa-server --restart unless-stopped -p 8001:8001 \
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
  -c "chmod 666 /usr/local/lib/python3.11/site-packages/efinance/data/search-cache.json 2>/dev/null || true \
      && exec /usr/local/bin/docker-entrypoint.sh python main.py --serve-only --host 0.0.0.0 --port 8001"
```

- `chmod` 段修复 efinance 缓存权限，**不可省略**。
- `FASTAPI_STARTUP_TIMEOUT`（代码默认 `15.0`）：挂载源码首次冷 import `api.app`（全量 `src/` + SearchService，实测约 9s）远大于原先硬编码的 3s，故 `main.py` 已把默认探针超时调到 15s。docker run 中显式 `-e FASTAPI_STARTUP_TIMEOUT=15` 是**可选**覆盖项（默认即够用）；仅当冷启动仍超时才需调大。
- `data/`、`logs/`、`.env` 挂载自运行态目录 `/home/haizh/opensource/daily_stock_analysis`；`/home/haizh/dsa-src` 必须是已完成 `git clone`/`pull` 的源码 checkout，否则容器缺 `main.py`/`src/`，服务无法启动。
- 仅当新代码引入新 pip 依赖时才需 `docker pull zhulinsen/daily_stock_analysis:latest` 重建镜像（依赖装在镜像 `/usr/local/lib`，源码挂载覆盖不到）。

#### 容器内命令（直连 docker exec，不用 dx）

远端 `docker exec` 经 SSH MCP 实测秒回、不挂起（短/长任务均正常），**直接用 `docker exec dsa-server ...`，无需 dx wrapper**。宿主机 `/home/haizh/local/bin/dx` 硬编码 `react-java-dev` + Java 环境白名单，不适用本容器。支持 `|`、`>`、`2>&1`、`&&`、`;`：

```bash
docker exec dsa-server bash -lc "echo OK; python3 --version"
docker exec dsa-server bash -lc "cd /app && python3 -m py_compile main.py && echo COMPILE_OK"
docker exec dsa-server bash -lc "cd /app && cat /app/.env | grep -v PASSWORD | head"   # 查配置（勿打印敏感值）
docker ps -a --filter name=dsa-server      # 查状态
docker logs --tail 50 dsa-server           # 查日志
```

#### 服务接口验证（curl）

```bash
curl -s http://127.0.0.1:8001/health || curl -s http://127.0.0.1:8001/   # 宿主机本机（经 SSH MCP）
curl -s http://192.168.201.148:8001/health                              # 本机访问远端服务
```

## 6. 验收边界

| 验证类型 | 执行位置 | 命令 |
| --- | --- | --- |
| 代码语法 / 单测 / CI gate | 远端临时挂载容器（`-v /home/haizh/dsa-src:/app`） | `./scripts/ci_gate.sh`、`python -m pytest -m "not network"`、`python -m py_compile` |
| 服务运行 / 接口验证 | 远端 dsa-server（经 SSH MCP） | `docker pull` + 重启 + `docker exec ...` + `curl http://192.168.201.148:8001` |
| Web 前端构建 | 远端挂载容器内 `apps/dsa-web` | `npm ci && npm run lint && npm run build` |
| 本机 | 仅代码分析/修改 + `git push` | 不执行运行/测试命令 |
