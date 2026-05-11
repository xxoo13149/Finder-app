# Changelog

所有重要更新都会记录在这里。版本号遵循接近语义化的方式：主版本表示方向性重构，次版本表示能力升级，补丁版本表示修复与稳定性提升。

## [0.2.1] - 2026-05-12

### Added

- 新增 Falcon 指标链路说明与 `.env.example` 占位：支持 lifetime PnL、ROI 和 `Falcon 15d` win rate 展示。
- 新增桌面快捷方式校正脚本：可以把桌面入口重新指向当前 `D:\Finder` 版本。
- 新增发布说明索引页：[docs/release/README.md](docs/release/README.md)。

### Changed

- README 改写为 GitHub 首页友好的中文介绍，突出项目定位、分析流程、功能矩阵、启动方式、环境变量和版本入口。
- 本周高盈利榜单在前端切换时会加载独立筛选参数，不再沿用普通分析表单值。
- Falcon Wallet 360 请求参数从 `limit=1` 调整为 `limit=5`，匹配当前 API 校验规则。

### Fixed

- 修复没有 `FALCON_API_TOKEN` 时 UI 只显示本地 `regional_trade_day_cashflow`，导致外部收益和胜率缺失的问题。
- 修复本周高盈利榜单提交时被普通分析 overrides 覆盖的问题。
- 修复桌面图标可能指向旧工作目录或旧启动器的问题。

### Validation

- `python -m unittest tests.test_falcon_client`
- `python -m py_compile src/polymarket_weather_tool/falcon_client.py src/polymarket_weather_tool/server.py`
- `python -m pytest tests/test_server.py::ServerConfigTests::test_build_config_for_run_applies_weekly_high_profit_mode_before_overrides tests/test_config_overrides.py`
- `npm run lint`
- `npm run build`

## [0.2.0] - 2026-05-09

### Added

- 新增接力分析模式：可以从某次历史运行的原始地址池中筛选钱包，按“系统核心标签 / 非核心标签”和“DeepSeek 已完成 / 未完成”重新建立独立任务。
- 新增运行状态诊断面板：展示来源地址池、候选预筛、当前批次、天气事件索引、full hydration 结果、DeepSeek gate reason 和跳过原因。
- 新增未完成任务续跑能力：已写入的钱包详情会保留，继续任务时跳过已完成钱包。
- 新增钱包列表分页读取，避免大运行结果一次性拉取过多详情。
- 新增运行摘要的轻量读取路径，超大钱包详情不会拖慢任务列表与状态页。

### Changed

- DeepSeek 深度解读升级到 `finder-weather-brief-v6`。
- 所有分析模式统一为“轻量筛选和打系统核心标签 -> 命中核心标签才进入重链路 -> DeepSeek 深度解读”。
- 天气事件默认索引上限从 `10000` 提升到 `100000`，降低因为事件池过小导致标签证据不足的概率。
- full history hydration 收紧为业务门槛触发：必须已经命中至少一个系统核心标签。
- 接力分析与 Smart Wallet 刷新拆成独立链路，避免地址库刷新和历史运行接力混用。
- README 重写为 GitHub 首页友好的中文说明，并新增版本记录入口。

### Fixed

- 修复 DeepSeek 结果回退到旧 prompt/cache 命名空间的问题。
- 修复非天气 `topTrades` 被用于天气画像的问题。
- 修复 `Science`、`Global Temp` 等主题组被写成“熟悉城市”的问题。
- 修复优质中文 DeepSeek 输出被模板化后处理覆盖的问题。
- 修复运行摘要读取超大钱包详情时可能卡住的问题。

### Validation

- `python -m unittest tests.test_finder_ai_generation`
- `python -m unittest tests.test_pipeline_smoke`
- `python -m unittest tests.test_server tests.test_upgrade_behaviors tests.test_finder_ai_generation`
- `python -m compileall src/polymarket_weather_tool`
- `npm run lint`
- `npm run build`

## [0.1.1] - 2026-05-07

### Added

- 新增 Cloudflare D1 可复用历史层：支持钱包 registry、交易 ledger、操作 ledger、gap metadata 和运行文档归档。
- 新增 GraphQL history provider fallback，用于补齐深度订单、成交与活动操作流。
- 新增归档前清理保护，重要运行产物可先归档再清理本地大文件。

### Changed

- 普通分析、盈利榜分析和 Smart Wallet 刷新可以共享累积历史数据。
- 本地优先路径保持不变，Cloudflare 作为可选复制和读取 fallback。

### Documentation

- 发布说明：[docs/release/20260507123000000_cloudflare_d1_history_persistence.md](docs/release/20260507123000000_cloudflare_d1_history_persistence.md)

## [0.1.0] - 2026-05-04

### Added

- 初始 Polymarket 天气赛道分析管线。
- 本地 HTTP API 与 React/Vite 控制台。
- 天气排行榜筛选、钱包指标、标签证据、报告产物和历史钱包名册。
- Finder AI 摘要入口与 SmartPro 同步基础能力。
