# DeepSeek Proxy 中转站

本地自用 DeepSeek API 代理，支持**响应缓存**、**前缀优化**、**多 Key 轮询**、**智能路由**、**费用统计面板**。

## 功能特性

| 模块 | 说明 |
|------|------|
| 响应缓存 | 基于请求 hash 的 SQLite 精确匹配缓存，24h TTL，LRU 淘汰 |
| 前缀优化 | 自动重排 messages 以最大化 DeepSeek 磁盘前缀缓存命中 |
| 多 Key 轮询 | Round-Robin + 429 指数退避 + 健康检查 |
| 智能路由 | model 别名映射 + 基于内容的 Flash/Pro 自动路由 |
| 费用统计 | 全量统计 token 消耗、缓存命中率、实际/理论费用、节省金额 |
| 仪表盘 | Chart.js 可视化面板，访问 `/dashboard` |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

复制 `.env.example` 为 `.env`，填入你的 API Key：

```bash
cp .env.example .env
```

编辑 `.env`：

```env
DEEPSEEK_API_KEYS=sk-your-deepseek-api-key-here
DEEPSEEK_BASE_URL=https://api.deepseek.com
LOG_LEVEL=INFO
```

也可以直接在 `config.yaml` 中配置（`deepseek.api_keys` 字段）。

### 3. 启动

```bash
python -m src.main
```

或使用 uvicorn：

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8000
```

服务启动后：

- 代理端点: `http://localhost:8000/v1/chat/completions`
- 仪表盘: `http://localhost:8000/dashboard`
- 健康检查: `http://localhost:8000/health`

## 客户端接入

任何兼容 OpenAI SDK 的客户端都可以接入，只需修改 base URL：

### OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-your-deepseek-api-key-here",  # 可任意，代理不验证此字段
    base_url="http://localhost:8000/v1",
)

response = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=[
        {"role": "system", "content": "你是一个有帮助的助手。"},
        {"role": "user", "content": "你好，介绍一下自己。"}
    ]
)

print(response.choices[0].message.content)
```

### LangChain / LangGraph

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model_name="deepseek-v4-flash",
    openai_api_key="dummy",        # 任意值
    openai_api_base="http://localhost:8000/v1",
)
```

### Cursor / Claude Code 等工具

在设置中将 `https://api.deepseek.com` 替换为 `http://localhost:8000` 即可。

## 配置说明

所有配置项在 `config.yaml` 中，敏感信息通过 `.env` 覆盖。

### 模型别名映射

```yaml
deepseek:
  model_aliases:
    "deepseek-chat": "deepseek-v4-flash"
    "deepseek-reasoner": "deepseek-v4-flash"
```

### 缓存策略

```yaml
cache:
  enabled: true
  db_path: "./data/cache.db"
  ttl_hours: 24
  max_entries: 10000
  cache_stream: false    # 流式响应暂不缓存
```

### 智能路由

```yaml
routing:
  default_model: "deepseek-v4-flash"
  content_rules:
    "推理": "deepseek-v4-pro"
    "代码生成": "deepseek-v4-pro"
    "简单对话": "deepseek-v4-flash"
```

### 多 Key 轮询

```yaml
key_pool:
  strategy: "round_robin"
  backoff_seconds: 60
  health_check_interval: 300
```

### 定价表

```yaml
pricing:
  "deepseek-v4-flash":
    input_per_million: 0.14
    cache_hit_input_per_million: 0.0028
    output_per_million: 0.28
```

## 费用节省原理

DeepSeek 的**磁盘前缀缓存**对 cache-hit tokens 提供 **98%** 折扣（$0.0028 vs $0.14 / 1M tokens）。

本代理在此基础上叠加**代理层响应缓存**：对于完全相同的请求（非 stream），直接返回缓存结果，零 API 调用消耗。

两层缓存叠加，RAG 等重复前缀场景可节省 **70~95%** 费用。

## API 接口

| 端点 | 说明 |
|------|------|
| `POST /v1/chat/completions` | OpenAI 兼容代理端点 |
| `GET /dashboard` | 可视化统计面板 |
| `GET /api/stats/summary` | 统计摘要 JSON |
| `GET /api/stats/daily?days=30` | 每日统计 JSON |
| `GET /api/stats/models` | 按模型分组统计 |
| `GET /health` | 健康检查 |

## 项目结构

```
deepseek-server/
├── src/
│   ├── main.py              FastAPI 入口
│   ├── config.py            YAML + .env 配置加载
│   ├── proxy.py             核心代理路由
│   ├── cache/
│   │   ├── response.py      SQLite 响应缓存
│   │   └── prefix.py        消息前缀优化
│   ├── router/
│   │   ├── key_pool.py      Key 轮询池
│   │   └── model_router.py  智能路由
│   ├── stats/
│   │   └── tracker.py       费用统计追踪
│   └── dashboard/
│       └── routes.py        仪表盘 API + HTML
├── config.yaml
├── .env.example
└── requirements.txt
```