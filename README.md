# Polymarket 天气赛道分析工具

这是一个面向 Polymarket 天气赛道的钱包筛选、交易分析、本地控制台与结果管理项目。

它把我们日常会反复做的几条链路收拢成一套可以重复执行的工作流：

1. 抓取天气赛道排行榜
2. 按盈亏、成交量、交易笔数、天气相关占比等条件做预筛选
3. 深入抓取候选钱包的交易、持仓、已平仓、奖励和标签证据
4. 在本地控制台里查看摘要、钱包列表、钱包详情、报告与历史记录
5. 记录已抓取地址，后续默认排除重复地址，减少无效重复分析
6. 支持把 Finder 结果同步到 Smart Pro 后台

## 当前能力

- Python 分析管线
- 本地 HTTP API
- React + Vite 控制台
- 历史任务与缓存清理
- 历史已抓取钱包名册
- 钱包详情用户名展示
- Finder AI 摘要 / 预览入口
- 本周高盈利榜单额外分析链路
- Smart Wallet Library 回流入口
- Smart Pro 同步能力

## 项目结构

- `src/polymarket_weather_tool/`：Python 核心逻辑、CLI、API
- `frontend/`：本地控制台前端
- `configs/default_config.json`：默认分析配置与规则
- `scripts/`：Windows 启动脚本
- `tests/`：Python 测试
- `docs/`：需求、方案、调研文档

## 环境要求

- Python 3.11+
- Node.js 18+
- npm

## 安装

### Python

```powershell
pip install -e .
```

或者直接按源码运行：

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m polymarket_weather_tool --config configs/default_config.json
```

### 前端

```powershell
Set-Location frontend
npm ci
```

## 启动方式

### 一次性运行分析

```powershell
polymarket-weather --config configs/default_config.json
```

常用示例：

```powershell
polymarket-weather `
  --config configs/default_config.json `
  --output-dir artifacts/demo-run `
  --target-count 5 `
  --fetch-limit 20 `
  --max-weather-events 200 `
  --max-wallet-offset 1000 `
  --concurrent-wallets 4 `
  --verbose
```

### 启动本地 API

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m polymarket_weather_tool.server --host 127.0.0.1 --port 41874
```

API 默认地址：

```text
http://127.0.0.1:41874
```

### 启动前端开发环境

```powershell
Set-Location frontend
npm run dev
```

前端默认地址：

```text
http://127.0.0.1:41873
```

### Windows 一键启动

项目内提供：

- `scripts/Open-PolymarketWeather.ps1`
- `scripts/Open-PolymarketWeather.vbs`

用于本机快速拉起后端和控制台。

## 控制台页面

- `Dashboard`：最近任务概览、标签分布、重点钱包
- `New analysis`：创建新任务，支持不同分析模式
- `Run status`：查看任务进度、日志与错误
- `Wallet list`：筛选结果列表，支持搜索和查看 AI 短摘
- `Wallet detail`：用户名、地址、指标、标签、证据、AI 研判
- `Reports`：查看报告与 JSON 产物
- `Rules`：规则与配置调整
- `History cleanup`：清理历史任务、缓存、临时文件、历史钱包名册

## 分析模式

### 1. 普通分析

默认链路，从天气排行榜中筛选符合条件的钱包。

### 2. 本周高盈利榜单

额外从“本周高盈利”榜单发起一条独立抓取与筛选链路，筛选口径与普通分析分开维护。

### 3. Smart Wallet Library 回流

用于导入后台地址库导出的 JSON，再回流到 Finder 重新跑任务与重新打标签。

## 历史已抓取钱包名册

项目会把“已经真实抓取过数据的钱包”记录到：

```text
artifacts/_wallet_registry/
```

作用：

- 后续筛选默认排除这些历史地址
- `include_wallets` 仍可强制放行
- 历史清理页会单独列出用户名、地址、首次记录时间、最近出现时间、分析次数

## Smart Pro 同步

Finder 支持把运行结果同步到 Smart Pro。

相关接口与能力已经在项目中预留：

- Smart Pro 配置状态查询
- Finder 结果打包为导入 payload
- 按钱包分批提交到 Smart Pro

## 环境变量

敏感信息不要直接写入仓库，请使用本地 `.env`。

已提供占位模板：

```text
.env.example
```

当前用到的环境变量主要包括：

- `ETHERSCAN_API_KEY`
- `DEEPSEEK_API_KEY`
- `DEEPSEEK_MODEL`
- `DEEPSEEK_BASE_URL`
- `DEEPSEEK_TIMEOUT_SECONDS`
- `SMART_PRO_BASE_URL`
- `SMART_PRO_FINDER_TOKEN`
- `SMART_PRO_ACCESS_CLIENT_ID`
- `SMART_PRO_ACCESS_CLIENT_SECRET`

说明：

- `.env` 不会提交到 GitHub
- `.env.example` 只保留占位值，可以提交

## 主要 API

- `GET /api/health`
- `GET /api/config/default`
- `PUT /api/config/default`
- `POST /api/runs`
- `GET /api/runs`
- `GET /api/runs/{run_id}`
- `GET /api/runs/{run_id}/summary`
- `GET /api/runs/{run_id}/wallets`
- `GET /api/runs/{run_id}/wallets/{wallet}`
- `GET /api/runs/{run_id}/report`
- `GET /api/runs/{run_id}/files`
- `GET /api/runs/{run_id}/artifact?path=...`
- `GET /api/history/cleanup`
- `POST /api/history/cleanup/delete`
- `GET /api/smart-pro/config`
- `POST /api/smart-pro/import/commit`

## 产物说明

每次运行通常会在 `artifacts/<run_id>/` 下生成：

- `report.txt`
- `leaderboard.json`
- `weather_events.json`
- `screening_records.json`
- `selected_wallets.json`
- `wallets/*.json`
- `resolved_config.json`
- `analysis_summary.json`
- `errors.json`
- `progress.log`

## 校验命令

### Python

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

### 前端

```powershell
Set-Location frontend
npm run build
```

## Git 说明

这次提交的目标是保留一个可以随时回退的稳定点。

约定：

- 提交代码与文档
- 不提交真实 API key
- 不提交本地 `.env`
- 可以提交 `.env.example` 占位模板

