# Polymarket 天气赛道分析工具

这是一个面向 Polymarket 天气赛道的钱包筛选、策略分析与本地控制台项目。

它把常见分析链路串成了一套可重复运行的流程：

1. 抓取 Polymarket 天气赛道排行榜
2. 按盈亏、成交量、交易数、天气相关占比等规则做预筛选
3. 深入抓取候选钱包的交易、持仓、已平仓、奖励与标签证据
4. 输出报告、摘要、钱包明细，并在本地前端中查看
5. 记录历史已抓取钱包，后续搜索默认排除重复地址

## 主要功能

- Python 分析管线
- 本地 HTTP API
- React + Vite 控制台
- 标签规则配置与保存
- 历史任务、运行缓存、临时产物清理
- 历史已抓取钱包名册管理

## 项目结构

- `src/polymarket_weather_tool/`：Python 核心逻辑、CLI、API
- `frontend/`：本地控制台前端
- `configs/default_config.json`：默认分析配置与标签规则
- `scripts/`：Windows 启动脚本
- `tests/`：Python 单元测试
- `docs/`：产品、报告、调研文档

## 环境要求

- Python 3.11+
- Node.js 18+
- npm

## 安装

### Python

```powershell
pip install -e .
```

如果不想安装为全局命令，也可以直接用源码运行：

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m polymarket_weather_tool --config configs/default_config.json
```

### 前端

```powershell
Set-Location frontend
npm ci
```

## 使用方式

### 运行一次分析

```powershell
polymarket-weather --config configs/default_config.json
```

常用参数示例：

```powershell
polymarket-weather `
  --config configs/default_config.json `
  --output-dir artifacts/demo-run `
  --target-count 3 `
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

启动后默认访问：

```text
http://127.0.0.1:41874
```

### 前端开发模式

```powershell
Set-Location frontend
npm run dev
```

开发地址默认是：

```text
http://127.0.0.1:41873
```

## 关键页面

- `Dashboard`：查看最近任务概览、标签分布、Top 钱包
- `New analysis`：配置并启动新的分析任务
- `Run status`：查看任务进度、日志与错误
- `Wallet list`：查看筛选结果，支持搜索、过滤、导出
- `Wallet detail`：查看单钱包详细指标、标签与证据
- `Reports`：查看报告与 JSON 产物
- `Rules`：编辑标签规则
- `History cleanup`：清理历史任务、缓存、临时文件、历史钱包名册

## 历史钱包名册

项目会将“已经真正抓取过数据的钱包”记录到 `artifacts/_wallet_registry/`。

这套机制用于减少重复抓取：

- 后续筛选默认排除这些历史地址
- `include_wallets` 仍可强制放行
- 历史清理页会单独展示“用户名 + 钱包地址 + 首次记录 + 最近出现 + 涉及分析次数”
- 删除或清空名册后，这些钱包会重新进入后续候选链路

## 主要 API

- `GET /api/health`：健康检查
- `GET /api/config/default`：读取默认配置
- `PUT /api/config/default`：保存默认配置
- `POST /api/runs`：启动分析任务
- `GET /api/runs`：查看历史任务
- `GET /api/runs/{run_id}`：查看任务详情
- `GET /api/runs/{run_id}/summary`：读取任务摘要
- `GET /api/runs/{run_id}/wallets`：读取钱包列表
- `GET /api/runs/{run_id}/wallets/{wallet}`：读取单钱包详情
- `GET /api/runs/{run_id}/report`：读取文本报告
- `GET /api/runs/{run_id}/files`：读取产物列表
- `GET /api/runs/{run_id}/artifact?path=...`：预览指定产物
- `GET /api/history/cleanup`：读取历史清理清单
- `POST /api/history/cleanup/delete`：执行删除或清理动作

## 产物说明

每次运行通常会在 `artifacts/<run_id>/` 下生成：

- `report.txt`：最终文本报告
- `leaderboard.json`：排行榜快照
- `weather_events.json`：天气事件索引
- `screening_records.json`：筛选过程记录
- `selected_wallets.json`：最终入选钱包
- `wallets/*.json`：单钱包深度分析结果
- `resolved_config.json`：本次运行生效配置
- `analysis_summary.json`：汇总摘要
- `errors.json`：错误信息
- `progress.log`：运行进度日志

## 验证命令

### Python

```powershell
python -m py_compile src\polymarket_weather_tool\analysis.py src\polymarket_weather_tool\server.py src\polymarket_weather_tool\history_registry.py
python -m unittest discover -s tests -p "test_*.py"
```

### 前端

```powershell
Set-Location frontend
npm run lint
npm run build
```

## Windows 快速启动

项目内提供了两个启动脚本：

- `scripts/Open-PolymarketWeather.ps1`
- `scripts/Open-PolymarketWeather.vbs`

适合本机直接启动本地服务与控制台。

## 说明

- `.env` 不会提交到仓库
- `artifacts/`、缓存目录、日志文件默认不进 Git
- 当前仓库以中文使用场景为主，README 也采用中文维护
