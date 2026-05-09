# Finder AI v6 与接力分析

版本标记：`finder-app-0.2.0`

## 更新内容

- DeepSeek prompt 命名空间恢复并固定到 `finder-weather-brief-v6`。
- DeepSeek 前置 prompt 重写为交易员桌面风格，不再只是把系统标签翻译成摘要。
- 新增 provider 输出校验：缺字段、空内容、乱码会被拒绝，并触发一次修复重试。
- 新增优质中文输出保护，避免本地后处理把 DeepSeek 的好结果改成模板句。
- 禁止非天气 `topTrades` 作为天气钱包画像证据。
- `Science`、`Global Temp` 等主题组不再被描述成“熟悉城市”。
- 所有分析模式统一重链路门槛：命中至少一个系统核心标签，才进入 full history hydration 和 DeepSeek。
- 新增接力分析模式，并与 Smart Wallet Library 刷新拆分为两条独立链路。
- 接力分析支持按系统核心标签与 DeepSeek 完成状态筛选。
- 新增未完成任务续跑能力和更详细的运行状态诊断。
- 天气事件默认索引上限提升到 `100000`。

## DeepSeek v6 契约

v6 prompt 的核心是“证据优先”：

- 主证据：`behaviorSnapshot`、`coverage`、`tradeSamples`。
- 辅助证据：`primarySignals`、`labelHits`、`profileSnapshot`、`operationAuditSnapshot`，以及确认相关的 `topTrades`。
- 输出字段：`strategyFocus`、`aiBriefShort`、`aiBriefNote`、`aiDeepNote`。
- 写作要求：自然简体中文、交易员视角、具体、有判断，并且每个强结论都能回到结构化证据。
- 边界：不编造事实、不推断链下动机、不假装知道城市专业知识、不用体育、政治、健康、票房等非天气市场支撑天气结论。

## 接力分析规则

接力分析用于从上一轮 Finder 运行继续工作。它应该面向那一轮的原始地址池，而不是只从已经完成的钱包详情文件里挑地址。

可用筛选：

- `core_label_filter=all|core|non_core`
- `deepseek_filter=all|completed|incomplete`

典型恢复路径：

1. 选择上一轮运行。
2. 筛选 `deepseek_filter=incomplete`。
3. 需要时再筛选 `core_label_filter=core`。
4. 启动新的独立接力分析任务。

## 校验

- `python -m unittest tests.test_finder_ai_generation`
- `python -m unittest tests.test_pipeline_smoke`
- `python -m unittest tests.test_server tests.test_upgrade_behaviors tests.test_finder_ai_generation`
- `python -m compileall src/polymarket_weather_tool`
- `npm run lint`
- `npm run build`

## 说明

- 已经用 v3 生成过的钱包详情不会被静默改写。如需重新生成，请从目标历史运行重新建立接力分析，让结果走 v6。
- 这次没有放宽 DeepSeek gate。修复目标是让证据链路更健康、更透明，而不是把不合格钱包强行送进 DeepSeek。
