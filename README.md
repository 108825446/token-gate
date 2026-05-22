# LLM Token Usage Service

基于 Python + FastAPI 的 LLM 代理转发与 Token 统计服务。支持 OpenAI / Anthropic 协议，提供请求代理、用量统计、Token 压缩、可视化查询一体方案。

---

## 目录结构

```
├── app/
│   ├── main.py           # FastAPI 路由 & 启动入口
│   ├── proxy.py          # 代理转发核心（同步/流式/usage 提取）
│   ├── service.py        # 业务层（统计 + 代理配置 CRUD）
│   ├── repository.py     # SQLite 数据访问层
│   ├── database.py       # 建表 & 自动迁移
│   ├── token_saver.py    # Token 压缩引擎（输入/输出）
│   ├── config.py         # 配置加载（proxy_catalog.json）
│   ├── models.py         # 数据模型 dataclass
│   └── schemas.py        # Pydantic 请求/响应模型
├── web/
│   ├── pages/
│   │   ├── dashboard.html    # 仪表盘页面
│   │   └── proxy-config.html # 代理配置管理页面
│   └── static/
│       ├── css/              # 样式
│       └── js/               # 前端逻辑
├── configs/
│   ├── proxy_catalog.json    # 代理实例配置
│   └── digicert-ca.pem       # 额外 CA 证书
├── data/
│   └── llm_usage.db          # SQLite 数据库（自动生成）
├── requirements.txt
└── README.md
```

---

## 快速启动

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

设置上游密钥（由代理统一持有时）：

```bash
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
```

启动服务：

```bash
uvicorn app.main:app --reload
```

访问入口：

| 地址 | 说明 |
|------|------|
| `http://127.0.0.1:8000/docs` | Swagger 文档 |
| `http://127.0.0.1:8000/health` | 健康检查 |
| `http://127.0.0.1:8000/dashboard` | 用量仪表盘 |
| `http://127.0.0.1:8000/proxy-config` | 代理配置管理 |

---

## 系统架构

```
┌──────────────────────────────────────────┐
│           用户 SDK / 业务服务              │
│  (OpenAI SDK / Anthropic SDK / curl)      │
└──────────────┬───────────────────────────┘
               │ POST /proxy/{provider}/{key}/{path}
               ▼
┌──────────────────────────────────────────┐
│  代理转发层 (proxy.py)                    │
│  ├─ 路由 & 鉴权                          │
│  ├─ Token Saver 输入压缩 (可选)            │
│  ├─ 同步 / 流式转发至上游                  │
│  └─ 响应 usage 自动提取                    │
├──────────────────────────────────────────┤
│  业务服务层 (service.py)                  │
│  ├─ UsageService：统计落库                 │
│  └─ ProxyCatalogService：代理配置 CRUD      │
├──────────────────────────────────────────┤
│  数据访问层 (repository.py / database.py) │
│  └─ SQLite 参数化查询                      │
└──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────┐   ┌──────────────────┐
│  llm_usage_log 表    │   │ proxy_catalog.json│
│  (SQLite)            │   │ (JSON 文件)       │
└─────────────────────┘   └──────────────────┘
```

---

## API 端点

### 系统

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |

### 页面

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/dashboard` | 用量仪表盘（聚合卡片 + 明细分页表） |
| GET | `/proxy-config` | 代理配置管理页（CRUD + 启禁） |

### 用量统计

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/usage/record` | 写入一条调用记录 |
| GET | `/api/v1/usage/list` | 查询调用明细（分页 + 多条件筛选） |
| GET | `/api/v1/stats/summary` | 聚合汇总（总数/成功/失败/Token 量/节省量） |
| GET | `/api/v1/stats/daily` | 按天趋势 |

### 代理配置

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/proxy/configs` | 代理配置列表（含接入地址） |
| POST | `/api/v1/proxy/configs` | 新增代理实例 |
| POST | `/api/v1/proxy/configs/reload` | 从磁盘热重载配置 |
| GET | `/api/v1/proxy/configs/{provider}/{proxy_key}` | 查询指定实例 |
| PUT | `/api/v1/proxy/configs/{provider}/{proxy_key}` | 更新指定实例 |
| DELETE | `/api/v1/proxy/configs/{provider}/{proxy_key}` | 删除指定实例 |

### 代理转发

| 方法 | 路径 | 说明 |
|------|------|------|
| 任意 | `/proxy/{provider}/{proxy_key}/{path}` | 代理转发入口，透传至上游 |

---

## 数据库

表 `llm_usage_log`：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| trace_id | TEXT | 业务追踪 ID |
| request_id | TEXT | 上游返回的请求 ID |
| biz_key | TEXT | 业务标识 |
| provider | TEXT | `openai` / `anthropic` |
| model | TEXT | 模型名 |
| endpoint | TEXT | 调用路径 |
| request_type | TEXT | `sync` / `stream` |
| user_id | TEXT | 用户标识 |
| tenant_id | TEXT | 租户标识 |
| latency_ms | INTEGER | 延迟（毫秒） |
| status | TEXT | `success` / `failed` / `interrupted` |
| error_code | TEXT | 错误码 |
| input_tokens | INTEGER | 输入 Token 数 |
| output_tokens | INTEGER | 输出 Token 数 |
| cache_creation_tokens | INTEGER | 缓存创建 Token |
| cache_read_tokens | INTEGER | 缓存读取 Token |
| reasoning_tokens | INTEGER | 推理 Token |
| total_tokens | INTEGER | 总计 Token |
| raw_usage | TEXT | 上游原始 usage JSON |
| input_tokens_saved | INTEGER | 输入端节省 Token |
| output_tokens_saved | INTEGER | 输出端节省 Token |
| created_at | TEXT | 创建时间（ISO 格式） |

---

## 代理配置

配置文件 `configs/proxy_catalog.json` 管理所有上游代理实例。每个实例独立配置，支持同一 provider 下多实例共存。

### 配置字段

| 字段 | 必填 | 说明 |
|------|------|------|
| `provider` | 是 | `openai` 或 `anthropic` |
| `proxy_key` | 否 | 实例标识（默认 `"default"`） |
| `display_name` | 否 | 展示名称（默认同 proxy_key） |
| `base_url` | 是 | 上游 API 地址 |
| `auth_header` | 是 | 鉴权头字段名（OpenAI `"Authorization"`，Anthropic `"x-api-key"`） |
| `api_key_env` | 否 | 环境变量名（从本机读取密钥） |
| `api_key_prefix` | 否 | API Key 前缀（OpenAI 需 `"Bearer"`，Anthropic 通常 `""`） |
| `forward_user_auth` | 否 | 是否透传用户请求里的鉴权头 |
| `timeout_seconds` | 否 | 上游超时（默认 60） |
| `enabled` | 否 | 启用/禁用 |
| `ssl_verify` | 否 | HTTPS 证书校验 |
| `static_headers` | 否 | 固定补充 header（如 Anthropic 的 `anthropic-version`） |
| `token_saver_enabled` | 否 | 是否启用 Token Saver 压缩 |
| `token_saver_input_level` | 否 | 输入压缩等级：`off` / `lite` / `full` / `ultra` |
| `token_saver_output_level` | 否 | 输出压缩等级：`off` / `lite` / `full` / `ultra` / `wenyan` |

### 配置示例

```json
{
  "providers": [
    {
      "provider": "openai",
      "proxy_key": "default",
      "base_url": "https://api.openai.com",
      "auth_header": "Authorization",
      "api_key_env": "OPENAI_API_KEY",
      "api_key_prefix": "Bearer",
      "forward_user_auth": false,
      "timeout_seconds": 60,
      "enabled": true,
      "ssl_verify": true,
      "static_headers": {},
      "token_saver_enabled": false,
      "token_saver_input_level": "full",
      "token_saver_output_level": "full"
    },
    {
      "provider": "anthropic",
      "proxy_key": "default",
      "display_name": "DeepSeek via Anthropic API",
      "base_url": "https://api.deepseek.com/anthropic",
      "auth_header": "x-api-key",
      "api_key_env": "ANTHROPIC_AUTH_TOKEN",
      "api_key_prefix": "",
      "forward_user_auth": true,
      "token_saver_enabled": true,
      "token_saver_input_level": "full",
      "token_saver_output_level": "wenyan"
    }
  ]
}
```

---

## Token Saver — Caveman 风格压缩

项目中一个独特功能：在代理层对 LLM 调用进行自动 Token 压缩，降低上游消耗。

### 输入压缩 (InputCompressor)

规则引擎压缩 prompt 文本，按等级递进：

| 等级 | 作用 |
|------|------|
| `lite` | 去除客套话、填充词（"sure!", "basically", "honestly"） |
| `full` | 在上基础上去除冠词（a/an/the）、简化冗余连接词（"in order to" → "to"）、去除强调副词 |
| `ultra` | 在上基础上简化动词短语（"is able to" → "can"、"make use of" → "use"） |

安全机制：
- 跳过代码块（`` ``` `` 内内容不变）
- 跳过 system message
- 节省率低于 5% 自动回退原文本

### 输出压缩 (OutputPromptInjector)

通过注入 system prompt 约束模型输出风格：

| 等级 | 效果 |
|------|------|
| `lite` | 简洁直述，去除填充词 |
| `full` | 洞穴人风格（fragment 句式，去除冠词/填充词） |
| `ultra` | 电报体（仅名词-动词-名词，省略系动词） |
| `wenyan` | **文言文输出** — 省去虚词、语气词，以文言文回复 |

安全机制：检测到 delete/drop/overwrite 等高风险关键词时自动跳过。

### 输出节省估算

| 等级 | 预估节省 |
|------|----------|
| lite | ~20% |
| full | ~50% |
| ultra | ~65% |
| wenyan | ~50% |

---

## 客户端接入

### OpenAI SDK

```python
from openai import OpenAI

client = OpenAI(
    api_key="dummy",  # 代理统一持有时可占位
    base_url="http://127.0.0.1:8000/proxy/openai/default/v1",
)

resp = client.chat.completions.create(
    model="gpt-4.1-mini",
    messages=[{"role": "user", "content": "hello"}],
)
```

### Anthropic SDK

```python
from anthropic import Anthropic

client = Anthropic(
    api_key="dummy",
    base_url="http://127.0.0.1:8000/proxy/anthropic/default",
)

resp = client.messages.create(
    model="claude-3-5-sonnet",
    max_tokens=256,
    messages=[{"role": "user", "content": "hello"}],
)
```

> 透传用户自己的 API Key：将配置中 `forward_user_auth` 设为 `true`，客户端传入真实 key 即可。

---

## 用法示例

### 写入用量记录

```bash
curl -X POST http://127.0.0.1:8000/api/v1/usage/record \
  -H "Content-Type: application/json" \
  -d '{
    "trace_id": "trace-001",
    "provider": "openai",
    "model": "gpt-4.1-mini",
    "biz_key": "chat-summary",
    "user_id": "u1001",
    "input_tokens": 1200,
    "output_tokens": 340,
    "raw_usage": {
      "prompt_tokens": 1200,
      "completion_tokens": 340,
      "total_tokens": 1540
    }
  }'
```

### 查询聚合

```bash
curl "http://127.0.0.1:8000/api/v1/stats/summary?provider=openai&start_date=2026-01-01"
```

### 查询明细

```bash
curl "http://127.0.0.1:8000/api/v1/usage/list?limit=20&offset=0&provider=openai"
```

---

## 流式代理统计

自动统计流式请求的 token 消耗：

| Provider | 机制 |
|----------|------|
| **OpenAI** stream=true | 自动注入 `stream_options.include_usage=true`，在流结束前的 usage chunk 提取数据 |
| **Anthropic** stream=true | 从 SSE 事件 `message_start` / `message_delta` 累计 input/output/cache token，流结束时落账 |

---

## 已知限制

- 主要面向文本接口，图片/音频等多模态账单字段尚未细分
- 流式统计依赖上游 SSE 事件的 usage 字段，极个别接口若不返回则无法精确统计
- 代理入口无鉴权（仅供内部网络使用）
- Token Saver 采用规则估算而非真实 tokenizer
- SQLite 在高并发写入场景下存在写锁争用
