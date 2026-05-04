# Finder AI 到 SmartPro / 扩展消费契约

## 文档目的

这份文档用于冻结当前 Finder 项目里已经落地的 AI 字段口径，方便后续三端协同：

1. Finder 本地钱包详情页
2. Finder 列表 / 报表等轻量展示面
3. SmartPro 同步链路与后续扩展程序消费

本文描述的是**当前已经实现的契约**，不是理想态设计稿。后续如果字段有新增或删减，应以新版文档为准。

## 当前范围

本期已经接入真实 DeepSeek 调用，模型固定为 `deepseek-v4-flash`。

当前 AI 结果只在**入选钱包**上生成，并围绕以下三个字段提供可展示内容：

- `strategyFocus`
- `aiBriefShort`
- `aiBriefNote`

其中：

- `strategyFocus` 是一句策略焦点，初始可由规则候选值提供，生成后可被 AI 覆盖
- `aiBriefShort` 是窄空间短摘要
- `aiBriefNote` 是详情页主摘要

`aiDeepNote` 目前仍属于保留字段，当前链路中没有正式生成内容，不应作为一期必依赖字段。

## 总体原则

### 1. 规则定结论，AI 写解释

当前筛选、命中、指标和结构化证据仍由 Finder 规则链路产出。  
DeepSeek 负责把这些结构化事实写成中文摘要，不负责替代标签判定。

### 2. 三层契约分开看

当前已经形成三层数据出口：

1. `wallet detail JSON.finder_ai`：完整契约，给详情页和内部链路使用
2. `selected_wallets.json / selection_record`：轻量契约，给列表和报表使用
3. `SmartPro sync payload.finderAi`：紧凑契约，给跨系统同步和扩展消费使用

### 3. 重字段不下发到外部链路

以下内容属于本地生成上下文或调试信息，不应作为 SmartPro / 扩展必需字段：

- `layeredInput`
- `briefGeneration`
- `providerMeta.cacheKey`
- `structured_materials`
- 原始交易明细、原始 label evaluation 全量内容

## 第一层：钱包详情 JSON 的 `finder_ai`

来源代码：

- `src/polymarket_weather_tool/analysis.py`
- `src/polymarket_weather_tool/finder_ai_contract.py`
- `src/polymarket_weather_tool/finder_ai_generation.py`

这是当前**最完整**的 AI 结果对象。

### 1. 基础身份字段

| 字段 | 含义 | 是否 AI 生成 | 是否建议外部消费 |
| --- | --- | --- | --- |
| `sourceName` | 来源名，当前固定为 `finder` | 否 | 是 |
| `runId` | 本次分析运行 ID | 否 | 是 |
| `normalizedAddress` | 标准化地址 | 否 | 是 |
| `wallet.address` | 钱包地址 | 否 | 是 |
| `wallet.displayName` | 当前展示名 | 否 | 是 |
| `wallet.alias` | 当前别名 | 否 | 是 |

### 2. AI 主结果字段

| 字段 | 含义 | 是否 AI 生成 | 说明 |
| --- | --- | --- | --- |
| `matched` | 当前是否命中 AI 结构化证据 | 否 | 由规则链路判断 |
| `strategyFocus` | 一句策略焦点 | 部分 | 初始有规则候选值，生成后可被 AI 覆盖 |
| `aiBriefShort` | 短摘要 | 是 | 适合列表、小卡片、扩展小空间 |
| `aiBriefNote` | 主摘要 | 是 | 适合详情页和中等空间展示 |
| `aiDeepNote` | 深摘要保留位 | 当前基本否 | 一期不依赖 |
| `evidenceLevel` | 证据级别 | 否 | 当前来自规则 / 门控 |
| `hasConflict` | 是否有信号冲突 | 否 | 当前由系统状态给出 |
| `needsReview` | 是否需要复核 | 否 | 当前由系统门控给出 |

### 3. 结构化证据字段

| 字段 | 含义 | 是否 AI 生成 | 是否建议外部消费 |
| --- | --- | --- | --- |
| `labels[]` | 结构化标签 | 否 | 是 |
| `primarySignals[]` | 主要信号 | 否 | 是 |
| `keyMetrics[]` | 关键指标 | 否 | 是 |
| `sourceExcerpt` | 证据摘录 | 否 | 是 |
| `weatherSignals` | 天气专项结构信号 | 否 | 是 |

### 4. 元数据字段

`providerMeta` 当前可能包含：

- `provider`
- `model`
- `promptVersion`
- `generatedAt`
- `inputHash`
- `generationScope`
- `outputSchemaVersion`
- `cacheKey`
- `requestId`

其中：

- 建议外部消费的只有 `provider`、`model`、`promptVersion`、`generatedAt`、`inputHash`、`generationScope`、`outputSchemaVersion`
- `cacheKey` 属于本地缓存键，不建议外传
- `requestId` 可以保留在本地详情中，但不作为外部依赖字段

### 5. 生成过程字段

当前本地详情还会保留：

- `layeredInput`
- `briefGeneration`

这两块用于记录输入分层、门控状态、缓存键和生成状态，方便本地审计与后续调试。  
它们属于**内部过程字段**，不是外部展示契约。

## 第二层：`selected_wallets.json` / `selection_record`

来源代码：

- `src/polymarket_weather_tool/analysis.py`

当前轻量同步只保留 5 个字段：

| 字段 | 来源 | 是否 AI 生成 | 用途 |
| --- | --- | --- | --- |
| `ai_strategy_focus` | `finder_ai.strategyFocus` | 部分 | 列表主文案候选 |
| `ai_brief_short` | `finder_ai.aiBriefShort` | 是 | 列表 / 报表短摘要 |
| `ai_needs_review` | `finder_ai.needsReview` | 否 | 状态标识 |
| `ai_has_conflict` | `finder_ai.hasConflict` | 否 | 状态标识 |
| `ai_evidence_level` | `finder_ai.evidenceLevel` | 否 | 证据强弱分层 |

这一层是故意轻量化的，当前**不会**下发以下内容：

- `aiBriefNote`
- `labels`
- `primarySignals`
- `keyMetrics`
- `providerMeta`
- `layeredInput`
- `briefGeneration`

所以列表页、报表页、导出页如果只依赖 `selected_wallets.json`，应优先按 `ai_brief_short` 和 `ai_strategy_focus` 来展示，不应假设这里一定有长摘要。

## 第三层：SmartPro 同步 payload 的 `finderAi`

来源代码：

- `src/polymarket_weather_tool/server.py`
- `src/polymarket_weather_tool/finder_ai_contract.py`

当前同步时会从钱包详情里的 `finder_ai` 压缩出一个 `finderAi` 对象。

### 1. 顶层保留字段

- `sourceName`
- `runId`
- `normalizedAddress`
- `matched`
- `strategyFocus`
- `aiBriefShort`
- `aiBriefNote`
- `aiDeepNote`
- `sourceExcerpt`
- `evidenceLevel`
- `hasConflict`
- `needsReview`

### 2. 嵌套保留字段

`wallet`：

- `address`
- `displayName`
- `alias`

`labels[]`：

- `kind`
- `value`
- `source`
- `evidence`

`primarySignals[]`：

- `key`
- `label`
- `matched`
- `reason`

`keyMetrics[]`：

- `key`
- `label`
- `value`

`weatherSignals`：

- `marketScope`
- `resolutionSource`
- `forecastBasis`
- `timingWindow`
- `edgeStyle`
- `evidenceQuality`
- `weatherDrivers`

`providerMeta`：

- `provider`
- `model`
- `promptVersion`
- `generatedAt`
- `inputHash`
- `generationScope`
- `outputSchemaVersion`

### 3. 明确不下发的字段

当前 compact `finderAi` 不下发：

- `layeredInput`
- `briefGeneration`
- `providerMeta.cacheKey`
- `providerMeta.requestId`
- `structured_materials`

这表示 SmartPro / 扩展侧应把 `finderAi` 看成**展示与同步契约**，而不是 Finder 内部完整调试对象。

## 三种展示面的字段优先级

### 1. 窄空间列表 / 扩展卡片

适用场景：

- 扩展程序卡片
- hover
- 紧凑列表副标题

字段优先级：

1. `aiBriefShort`
2. `strategyFocus`
3. `aiBriefNote`
4. 回退到规则摘要或 `sourceExcerpt`

状态优先级：

1. `needsReview`
2. `hasConflict`
3. `evidenceLevel`

展示原则：

- 有 `aiBriefShort` 时优先只显示这一句
- `needsReview = true` 时必须给出“需复核”
- `hasConflict = true` 时必须给出“信号冲突”
- `evidenceLevel = insufficient` 时不要渲染成明确判断口吻

### 2. 中等空间管理列表

适用场景：

- Finder 钱包列表
- Finder 报表列表
- SmartPro 后台管理列表

字段优先级：

1. `strategyFocus`
2. `aiBriefShort`
3. `aiBriefNote`

建议组合：

- 主文案：`strategyFocus`
- 副文案：`aiBriefShort`
- 状态：`needsReview` / `hasConflict` / `evidenceLevel`
- 时间：`providerMeta.generatedAt`

回退规则：

- 没有 `strategyFocus` 时，用 `aiBriefShort`
- 没有 `aiBriefShort` 时，用 `aiBriefNote` 的前一句
- 没有 `generatedAt` 时，不要伪造成 AI 生成时间

### 3. 钱包详情页

适用场景：

- Finder 钱包详情
- SmartPro 地址详情
- 扩展程序展开态详情

字段优先级：

1. `strategyFocus`
2. `aiBriefNote`
3. `aiBriefShort`
4. `primarySignals` / `keyMetrics` / `sourceExcerpt`

建议结构：

- 顶部一句话定位：`strategyFocus`
- 主体解释：`aiBriefNote`
- 侧栏状态：`needsReview`、`hasConflict`、`evidenceLevel`
- 时间信息：`providerMeta.generatedAt`
- 结构化支撑：`primarySignals`、`keyMetrics`、`sourceExcerpt`

如果三段文案都为空，应直接回退到结构化证据，不要拼装伪 AI 文案。

## 字段生成责任说明

### 当前由 AI 生成或直接参与覆盖的字段

- `strategyFocus`
- `aiBriefShort`
- `aiBriefNote`

### 当前由规则 / 系统链路提供的字段

- `matched`
- `labels`
- `primarySignals`
- `keyMetrics`
- `sourceExcerpt`
- `weatherSignals`
- `evidenceLevel`
- `hasConflict`
- `needsReview`
- `providerMeta.promptVersion`
- `providerMeta.inputHash`
- `providerMeta.generationScope`
- `providerMeta.outputSchemaVersion`

### 当前为内部运行态字段

- `layeredInput`
- `briefGeneration`
- `providerMeta.cacheKey`

## 当前测试覆盖情况

来源测试：

- `tests/test_pipeline_smoke.py`
- `tests/test_server.py`

### 已有覆盖

当前已经验证了这些关键点：

1. pipeline 会产出 `finder_ai`
2. `runId`、`normalizedAddress`、`providerMeta.promptVersion`、`inputHash`、`layeredInput`、`briefGeneration` 会写入本地详情
3. 选中钱包会走 AI 生成插点，并把 `aiBriefShort`、`aiBriefNote`、`generatedAt` 回填到本地详情
4. `selected_wallets.json` 会同步 `ai_brief_short` 和 `ai_strategy_focus`
5. SmartPro compact `finderAi` 会保留短摘要、主摘要、结构化信号，同时剔除 `layeredInput` 和 `briefGeneration`

### 还没有完全冻结的部分

以下内容目前更多是代码约定，还不是完整测试契约：

1. DeepSeek 真实调用成功 / 失败 / 缓存命中分支
2. `briefGeneration` 的所有边界状态
3. `selected_wallets.json` 里 `ai_needs_review`、`ai_has_conflict`、`ai_evidence_level` 的专项断言
4. `finderAi.providerMeta` 保留字段的完整冻结
5. `aiDeepNote` 的正式生成与消费

## 一期消费建议

如果 SmartPro 或扩展程序要按当前版本直接接入，建议遵守下面的最小依赖口径：

### 必依赖

- `normalizedAddress`
- `wallet.address`
- `wallet.displayName`
- `strategyFocus`
- `aiBriefShort`
- `aiBriefNote`
- `evidenceLevel`
- `hasConflict`
- `needsReview`
- `providerMeta.generatedAt`

### 可选增强

- `labels`
- `primarySignals`
- `keyMetrics`
- `sourceExcerpt`
- `weatherSignals`

### 一期不要依赖

- `aiDeepNote`
- `layeredInput`
- `briefGeneration`
- `providerMeta.cacheKey`
- `providerMeta.requestId`

## 当前结论

当前 Finder AI 契约已经可以支撑：

1. Finder 本地详情页展示 AI 摘要和状态
2. Finder 列表 / 报表展示短摘要
3. SmartPro 接收紧凑版 `finderAi`
4. 扩展程序基于 `aiBriefShort` 和 `aiBriefNote` 做轻量到中等空间展示

当前最重要的消费口径可以压缩成一句话：

**列表看 `aiBriefShort`，详情看 `aiBriefNote`，状态看 `needsReview / hasConflict / evidenceLevel`，地址主键永远看 `normalizedAddress`。**
