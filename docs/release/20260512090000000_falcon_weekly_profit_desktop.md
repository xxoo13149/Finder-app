# Falcon、周榜筛选和桌面入口

版本标记：`finder-app-0.2.1`

## 更新内容

- Falcon 指标链路打通：`FALCON_API_TOKEN` 可从本地 `.env` 加载，用于 wallet detail 和钱包列表展示外部指标。
- Falcon Wallet 360 请求参数修复：`pagination.limit` 从 `1` 调整为 `5`，避免 API 校验失败导致 win rate 为空。
- 本周高盈利榜单前端表单修复：切换到周榜模式时加载 `analysis_modes.weekly_high_profit` 的独立筛选参数。
- 保存默认配置时区分普通分析和本周高盈利榜单：周榜参数写回 `analysis_modes.weekly_high_profit`，不污染顶层普通分析配置。
- 桌面快捷方式校正：`打开 Polymarket 天气分析工具.lnk` 指向当前 `D:\Finder\scripts\Open-PolymarketWeather.vbs`。
- README 改写为 GitHub 首页友好的中文说明，并新增版本地图。

## Falcon 指标口径

| 字段 | 来源 | 说明 |
| --- | --- | --- |
| `falcon_total_pnl` | Falcon lifetime | 钱包累计 PnL |
| `falcon_total_roi` | Falcon lifetime | 钱包累计 ROI |
| `falcon_win_rate` | Falcon Wallet 360 | 默认 15 天窗口胜率 |
| `falcon_win_rate_window_label` | Finder config | 默认 `Falcon 15d` |

如果本地没有 `FALCON_API_TOKEN`，分析仍会继续，但 Falcon 指标为空，界面会回落到本地计算的交易胜率。

## 本周高盈利榜单默认筛选

| 字段 | 默认值 |
| --- | --- |
| `time_period` | `WEEK` |
| `min_pnl` / `max_pnl` | `25` / `2000` |
| `min_volume` / `max_volume` | `500` / `1000000` |
| `min_traded_count` / `max_traded_count` | `5` / `2000` |
| `min_weather_trade_ratio` | `0.2` |
| `min_weather_notional_ratio` | `0.45` |
| `weather_focus_mode` | `trade_or_notional` |

这些参数按周榜候选池设计，不应与普通分析的日常筛选条件混用。

## 校验

- `python -m unittest tests.test_falcon_client`
- `python -m py_compile src/polymarket_weather_tool/falcon_client.py src/polymarket_weather_tool/server.py`
- `python -m pytest tests/test_server.py::ServerConfigTests::test_build_config_for_run_applies_weekly_high_profit_mode_before_overrides tests/test_config_overrides.py`
- `npm run lint`
- `npm run build`

## 备注

- 本次没有改变普通分析的筛选条件。
- 本地 `.env` 不进入 GitHub；`.env.example` 只提供变量占位。
- 桌面启动器会检查前端构建是否过期，必要时自动构建后再打开控制台。
