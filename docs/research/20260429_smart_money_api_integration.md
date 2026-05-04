# smart-money-pro API 接入方案

## 结论

推荐把 `smart-money-pro` 定位为**地址标签主系统**，由它对 `Finder-app` 开放 API；`Finder-app` 继续负责**分析、筛选、证据生成与运行产物**。

不建议一开始做：

1. `smart-money-pro` 反向拉取 `Finder-app`
2. 两边都能改同一批钱包字段的真双向同步
3. 一上来就单独上一个中间同步服务

最稳的路线是：

1. 阶段 1：半自动单向导入
2. 阶段 2：`smart-money-pro` 开私有 ingest API 给 `Finder-app`
3. 阶段 3：再抽象成平台化开放接口

## 为什么应该由 smart-money-pro 开 API

### 1. smart-money-pro 更像主库

`smart-money-pro` 已经有长期钱包主档、标签、备注、观察名单、导入批次和版本控制这些核心表：

- `wallets`
- `wallet_user_labels`
- `wallet_notes`
- `wallet_watchlist`
- `wallet_import_batches`
- `dataset_versions`

参考：

- [schema.ts](C:/Users/32360/AppData/Local/Temp/codex-smart-money-pro/packages/data/src/schema.ts:1)
- [schema.ts](C:/Users/32360/AppData/Local/Temp/codex-smart-money-pro/packages/data/src/schema.ts:38)
- [schema.ts](C:/Users/32360/AppData/Local/Temp/codex-smart-money-pro/packages/data/src/schema.ts:79)
- [schema.ts](C:/Users/32360/AppData/Local/Temp/codex-smart-money-pro/packages/data/src/schema.ts:184)

它还已经有数据版本号维护能力，适合给外部系统做缓存和增量读取：

- [repository.ts](C:/Users/32360/AppData/Local/Temp/codex-smart-money-pro/packages/data/src/repository.ts:1043)

### 2. smart-money-pro 已经有可复用的查询 API 雏形

它在 worker 层已经实现了标签查询和地址搜索能力，并带鉴权、版本、缓存和 ETag：

- `handleLabelsLookup`
- `handleAddressSearch`

参考：

- [routes.ts](C:/Users/32360/AppData/Local/Temp/codex-smart-money-pro/apps/worker/src/routes.ts:804)
- [routes.ts](C:/Users/32360/AppData/Local/Temp/codex-smart-money-pro/apps/worker/src/routes.ts:888)
- [repository.ts](C:/Users/32360/AppData/Local/Temp/codex-smart-money-pro/packages/data/src/repository.ts:2639)
- [repository.ts](C:/Users/32360/AppData/Local/Temp/codex-smart-money-pro/packages/data/src/repository.ts:2720)

### 3. Finder-app 更像分析工作台

当前 `Finder-app` 的数据组织方式是按 run 和 artifacts 走的，本地运行态明显，适合做“分析来源”，不适合直接充当中心主库：

- run 列表 / run 详情 / summary / wallets 都围绕 `/api/runs/...`
- 结果落地到 `artifacts/<run_id>/selected_wallets.json` 与钱包详情文件

参考：

- [server.py](C:/Users/32360/OneDrive/Documents/New project 3/src/polymarket_weather_tool/server.py:1195)
- [server.py](C:/Users/32360/OneDrive/Documents/New project 3/src/polymarket_weather_tool/server.py:134)
- [analysis.py](C:/Users/32360/OneDrive/Documents/New project 3/src/polymarket_weather_tool/analysis.py:190)
- [api.ts](C:/Users/32360/OneDrive/Documents/New project 3/frontend/src/lib/api.ts:406)

另外它目前的服务入口主要还是本地 HTTP 服务，鉴权只做到浏览器来源白名单，不具备直接承担外部开放写接口的条件：

- [server.py](C:/Users/32360/OneDrive/Documents/New project 3/src/polymarket_weather_tool/server.py:1410)

## 两个系统的职责边界

### smart-money-pro 负责

1. 钱包主档
2. 展示名 / alias / 长期备注
3. 人工标签与审核
4. watchlist / 历史治理
5. 标签查询 API
6. 同步审计、版本、回滚

### Finder-app 负责

1. 榜单抓取
2. 地址筛选
3. 运行级分析
4. 每次 run 的筛选结果
5. 证据与说明文本
6. 候选系统标签与观察结果输出

一句话说，就是：

- `smart-money-pro` 管“长期认知”
- `Finder-app` 管“本次分析发现”

## 推荐的数据所有权

### smart-money-pro 主拥有

1. `display_name`
2. `alias`
3. `bio`
4. `strategy_focus`
5. `team_note`
6. `watchlisted`
7. 人工标签
8. 删除状态

### Finder-app 只提供观察值或建议值

1. `user_name`
2. `x_username`
3. `run_count`
4. 本次筛选标签
5. 证据摘要
6. 风险判断

当前 `Finder-app` 已经在历史记录里保存了 `user_name`、`x_username`、`run_count` 等字段，更像“观察到的外部画像”，不适合直接覆盖主库展示名：

- [analysis.py](C:/Users/32360/OneDrive/Documents/New project 3/src/polymarket_weather_tool/analysis.py:911)
- [analysis.py](C:/Users/32360/OneDrive/Documents/New project 3/src/polymarket_weather_tool/analysis.py:1282)

## API 方案

### 第一层：只读 API

先由 `smart-money-pro` 对 `Finder-app` 提供统一只读查询。

建议最小接口：

1. `POST /openapi/v1/labels/lookup`
2. `GET /openapi/v1/addresses/search?q=...`
3. `GET /openapi/v1/wallets/by-address/:chain/:address`

建议返回：

- 钱包主档
- 展示名
- alias
- 标签列表
- watchlist 状态
- note 摘要
- `dataset_version`
- `updated_at`

### 第二层：私有写入 API

等只读稳定后，再加给 `Finder-app` 的私有 ingest 接口：

1. `POST /openapi/v1/sync/wallet-delta`
2. `GET /openapi/v1/sync/jobs/:job_id`

这个接口只接受：

1. 新发现地址
2. 系统标签候选
3. 证据摘要
4. 观察到的用户名/X 用户名
5. 来源 run_id

不接受：

1. 全量交易流水
2. 全量事件明细
3. 原始快照
4. 直接覆盖人工标签的写法

## 推荐的同步载荷

```json
{
  "source_system": "finder-app",
  "schema_version": "2026-04-29",
  "request_id": "uuid",
  "sent_at": "2026-04-29T12:34:56.789Z",
  "events": [
    {
      "event_id": "uuid",
      "entity": {
        "chain": "polygon",
        "address": "0x..."
      },
      "source_run_id": "run-20260429-001",
      "observed_at": "2026-04-29T12:33:10.000Z",
      "profile_observation": {
        "user_name": "foo",
        "x_username": "bar"
      },
      "system_labels": {
        "mode": "replace",
        "source_namespace": "finder_analysis",
        "revision": "sha256:...",
        "labels": [
          {
            "kind": "style",
            "name": "高频交易",
            "value": "是",
            "evidence": "最近 7 日交易频率高于阈值"
          }
        ]
      },
      "evidence_summary": {
        "report_excerpt": "...",
        "wallet_artifact_path": "artifacts/run-20260429-001/wallets/0x123.json"
      }
    }
  ]
}
```

## 同步协议的关键治理规则

### 1. 权限模型

读接口和写接口分开：

1. 读接口：沿用现有 worker 鉴权模型
2. 写接口：单独的 service credential

建议写接口请求头：

- `X-Key-Id`
- `X-Timestamp`
- `X-Nonce`
- `X-Signature`
- `Idempotency-Key`

写接口不要复用后台管理员登录态。

### 2. 幂等

建议幂等键格式：

`source_system:entity_type:chain:normalized_address:operation:source_run_id:payload_sha256`

规则：

1. 同 key 同 body：返回首次结果
2. 同 key 不同 body：返回 `409`

### 3. 版本与时间戳

除了全局 `dataset_version`，建议再有：

1. `record_version`
2. `observed_at`
3. `source_updated_at`
4. `ingested_at`
5. `deleted_at`

这样才能区分“源头何时观察到”和“主库何时入库”。

### 4. 删除语义

地址只做软删除，不做物理删。

建议字段：

1. `deleted_at`
2. `deleted_by`
3. `delete_reason`
4. `curation_status=deleted`

并且必须明确：

“某次同步里没出现”绝不等于“应该删除”。

只有显式 `full_snapshot=true` 且指定 `snapshot_scope` 时，才允许快照式对账。

### 5. 冲突规则

建议优先级：

1. 人工标签 > 系统标签
2. 主库主档字段 > Finder 观察值
3. 新 revision > 旧 revision
4. 同来源命名空间内允许 replace
5. 不同来源命名空间之间不能互相覆盖

这里有一个很关键的点：`smart-money-pro` 现有 `wallet_user_labels.source` 只有 `system/user` 两层，后面如果要接多个自动来源，建议补一个 `source_namespace` 或等价字段，否则不同自动来源会互相冲掉。

参考：

- [repository.ts](C:/Users/32360/AppData/Local/Temp/codex-smart-money-pro/packages/data/src/repository.ts:1717)
- [repository.ts](C:/Users/32360/AppData/Local/Temp/codex-smart-money-pro/packages/data/src/repository.ts:1761)

## 三阶段实施路线

### 阶段 1：半自动单向导入

目标：

先把 `selected_wallets + 钱包摘要 + 标签 + 证据` 导入主库。

做法：

1. `Finder-app` 导出适配后的轻量 JSON
2. `smart-money-pro` 复用现有 import commit 逻辑入库

参考：

- [route.ts](C:/Users/32360/AppData/Local/Temp/codex-smart-money-pro/apps/web/app/api/wallets/import/commit/route.ts:7)

### 阶段 2：私有自动同步

目标：

让分析完成后自动把 delta 推给 `smart-money-pro`。

做法：

1. 新增 `wallet-delta` 私有写接口
2. 加幂等键
3. 加同步 job 状态查询
4. 按批次回滚

### 阶段 3：平台化开放

目标：

后续考虑接更多分析器或后台来源。

做法：

1. provider registry
2. schema version
3. scope 权限
4. 限流与审计
5. webhook / replay

## 为什么不建议反向拉取

如果让 `smart-money-pro` 主动拉 `Finder-app`，会出现几个问题：

1. 把云端主库绑死在本地运行服务上
2. 需要处理本地可达性与安全暴露
3. 需要理解 run/artifacts 语义
4. 大文件制品不适合轮询同步
5. 排障链路会更绕

## 当前最值得先做的 5 件事

1. 在 `smart-money-pro` 定义开放 API 边界，只做标签与地址画像
2. 给标签写入补 `source_namespace`
3. 先做 `lookup/search/detail` 三个只读接口
4. 设计 `wallet-delta` 私有写协议
5. 在 `Finder-app` 增加“分析结果投影导出层”，只输出同步需要的轻量字段

## 一句话拍板

先把 `smart-money-pro` 做成地址标签主系统，让 `Finder-app` 像分析引擎一样把结果单向推过去；先读后写，先轻量投影后自动同步，先治理字段所有权再谈开放平台。
