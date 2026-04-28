# Polymarket 天气分析控制台

这是 Python `polymarket_weather_tool` 的 React/Vite 本地前端。

## 启动方式

先在项目根目录启动 Python API：

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m polymarket_weather_tool.server --host 127.0.0.1 --port 41874
```

再在 `frontend/` 目录启动前端：

```powershell
npm ci
npm run dev
```

默认访问地址：

```text
http://127.0.0.1:41873
```

如果 41873 端口被占用：

```powershell
npx vite --host 127.0.0.1 --port 41873 --strictPort
```

## API 地址

开发环境默认通过 Vite 代理访问：

```text
/api -> http://127.0.0.1:41874
```

如需手动指定，可以创建 `.env.local`：

```env
VITE_API_BASE_URL=http://127.0.0.1:41874
```

## 页面对应关系

- 控制台总览：`GET /api/runs`、`GET /api/runs/{run_id}`、`GET /api/runs/{run_id}/summary`
- 新建分析：`GET /api/config/default`、`POST /api/runs`、`PUT /api/config/default`
- 运行状态：`GET /api/runs/{run_id}`、`GET /api/runs/{run_id}/artifact?path=errors.json`
- 钱包列表：`GET /api/runs/{run_id}/wallets`、`GET /api/runs/{run_id}/artifact?path=selected_wallets.json`
- 钱包详情：`GET /api/runs/{run_id}/wallets/{wallet}`
- 报告产物：`GET /api/runs/{run_id}/files`、`GET /api/runs/{run_id}/report`、`GET /api/runs/{run_id}/artifact?path=...`
- 标签规则：`GET /api/config/default`、`PUT /api/config/default`

## 检查命令

```powershell
npm run lint
npm run build
```
