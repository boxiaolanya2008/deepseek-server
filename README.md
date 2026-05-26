# DeepSeek Proxy 中转站

本地自用 DeepSeek API 代理，支持**双层缓存**、**前缀优化**、**多 Key 轮询**、**智能路由**、**配置热重载**。

## 功能特性

| 模块 | 说明 |
|------|------|
| 双层缓存 | 代理层 SQLite 精确匹配缓存 + DeepSeek 磁盘前缀缓存，实测节省 47% 费用 |
| 前缀优化 | 自动归一化 system prompt + 去重 tool 结果，最大化 DeepSeek 前缀缓存命中 |
| Stream 缓存 | 流式请求转非 stream 请求以便缓存，后续相同请求直接返回缓存结果 |
| 多 Key 轮询 | Round-Robin + 429 指数退避 + 健康检查 |
| 智能路由 | model 别名映射 + 基于内容的 Flash/Pro 自动路由 + 强制模型 |
| 配置热重载 | 修改 config.yaml 自动生效，无需重启服务 |

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
    "deepseek-v3": "deepseek-v4-flash"
    "r1": "deepseek-v4-flash"
```

### 缓存策略

```yaml
cache:
  enabled: true
  db_path: "./data/cache.db"
  ttl_hours: 24
  max_entries: 10000
  cache_stream: true    # 开启后流式请求也能被缓存
```

### 智能路由

```yaml
routing:
  default_model: "deepseek-v4-flash"
  force_model: "deepseek-v4-flash"    # 全量走 Flash 节省 80%+
  content_rules: {}                    # 基于内容的路由规则（当前为空）
```

### 多 Key 轮询

```yaml
key_pool:
  strategy: "round_robin"    # round_robin | random
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
  "deepseek-v4-pro":
    input_per_million: 0.435
    cache_hit_input_per_million: 0.003625
    output_per_million: 0.87
```

## 费用节省原理

DeepSeek 的**磁盘前缀缓存**对 cache-hit tokens 提供 **98%** 折扣（$0.0028 vs $0.14 / 1M tokens）。

本代理在此基础上叠加**代理层响应缓存**：

- **非 stream 请求**：完全相同的请求直接返回缓存结果，零 API 调用消耗
- **stream 请求**：开启 `cache_stream` 后，流式请求转为非 stream 请求以便缓存，后续相同请求直接返回缓存结果

两层缓存叠加，实测可节省 **47%** 费用（RAG 等重复前缀场景可进一步提升至 70~95%）。

## API 接口

| 端点 | 说明 |
|------|------|
| `POST /v1/chat/completions` | OpenAI 兼容代理端点 |
| `GET /v1/models` | OpenAI 兼容模型列表 |
| `GET /health` | 健康检查 |

## 项目结构

```
deepseek-server/
├── src/
│   ├── main.py              FastAPI 入口 + 配置热重载
│   ├── config.py            YAML + .env 双源配置加载
│   ├── proxy.py             核心代理路由 (含 stream 缓存转换)
│   ├── cache/
│   │   ├── response.py      SQLite 响应缓存
│   │   └── prefix.py        消息前缀优化 (system 归一化 + tool 去重)
│   ├── router/
│   │   ├── key_pool.py      Key 轮询池
│   │   └── model_router.py  智能路由
│   └── stats/
│       └── tracker.py       费用统计追踪
├── config.yaml
├── .env.example
└── requirements.txt
```