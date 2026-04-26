# Cline Proxy

一个轻量级本地 API 代理，专为 **Cline (VS Code extension)** 设计。

## 解决的问题

1. **Cline 的 OpenAI Compatible 模式无法调节 top_p 等超参数** — 代理拦截请求后按需覆盖
2. **切换 API Provider 需要反复填写 URL 和 API Key** — 配置统一管理，Cline 只需指向 localhost
3. **不同 provider 对参数的容忍度不同** — 白名单机制过滤不支持的参数，避免上游报错

## 工作原理

```
Cline (base_url=http://localhost:{PORT}/v1)
        │ POST /v1/chat/completions {model, messages, ...}
        ▼
   ┌───────────────────┐
   │   Cline Proxy     │  1. 查路由表 → 确定 provider
   │   (localhost)     │  2. 白名单过滤 → 移除不支持的参数
   │                   │  3. 覆盖超参数 → 应用预设值
   │                   │  4. 替换 API Key 和 Base URL
   │                   │  5. 转发请求 → 透传响应
   └───────────────────┘
        │
        ▼
   实际 API (DeepSeek / OpenAI / 小米 / …)
```

## 快速开始

### 1. 创建虚拟环境并安装依赖

```powershell
# 创建 venv
python -m venv venv

# 激活 venv
# PowerShell:
.\venv\Scripts\Activate.ps1
# CMD:
# venv\Scripts\activate.bat

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置

```bash
# 复制配置示例
# PowerShell / CMD:
copy config.example.yaml config.yaml
# Linux / macOS:
# cp config.example.yaml config.yaml
```

编辑 `config.yaml`：

```yaml
# 设置 API Key（支持环境变量 ${VAR_NAME}、文件 @file:path、或明文）
providers:
  deepseek:
    api_key: ${DEEPSEEK_API_KEY}   # 从环境变量读取
  openai:
    api_key: ${OPENAI_API_KEY}
  xiaomi:
    api_key: sk-xxxxx              # 或直接写入
```

```bash
# 先设置环境变量（Windows CMD）
set DEEPSEEK_API_KEY=sk-your-key-here
set OPENAI_API_KEY=sk-your-key-here

# 或者 PowerShell
$env:DEEPSEEK_API_KEY = "sk-your-key-here"
```

### 3. 启动

```bash
python proxy.py
```

启动后会显示：
```
============================================
Cline Proxy is ready!
Set Base URL in Cline to:
  http://localhost:52340/v1
============================================
```

### 4. 配置 Cline

| 设置项 | 值 |
|--------|-----|
| API Provider | **OpenAI Compatible** |
| Base URL | `http://localhost:52340/v1` （以实际输出为准） |
| API Key | 任意值（代理会使用配置中的 key） |
| Model ID | 路由表中的 model 名，如 `deepseek-chat`、`gpt-4o` |

## 配置文件详解

### model_routing — 模型路由表

```yaml
model_routing:
  deepseek-chat: deepseek    # Cline 填 "deepseek-chat" → 路由到 deepseek provider
  gpt-4o: openai             # Cline 填 "gpt-4o" → 路由到 openai provider
  MiMo-7B-RL: xiaomi         # Cline 填 "MiMo-7B-RL" → 路由到 xiaomi provider
```

**重要**：`model` 字段原样透传，不做任何拆分或修改。支持任何格式的模型名（如 `org/some-model`）。

### allowed_params — 参数白名单

每个 provider 定义自己支持的参数，不在白名单中的参数会被过滤掉。

```yaml
providers:
  deepseek:
    allowed_params:
      - temperature
      - top_p       # 白名单中 → 保留并可覆盖
      # top_k 不在白名单中 → 自动过滤，防止 DeepSeek 报错
```

以下核心参数**始终保留**，不受白名单影响：
`model`, `messages`, `stream`, `max_tokens`, `tools`, `tool_choice`, `response_format`, `stop`, `n`

### models — 超参数覆盖

```yaml
models:
  deepseek-chat:
    temperature: 0.7       # Cline 的 temperature 会被覆盖为 0.7
    top_p: 0.9             # Cline 未设置 top_p → 添加 top_p=0.9
  deepseek-coder:
    temperature: 0.3       # 只覆盖 temperature，其他参数保留 Cline 原始值
```

**原则**：只覆盖配置中显式出现的字段。未配置的字段保持 Cline 原始值。

## 命令行参数

```bash
python proxy.py                    # 使用 config.yaml
python proxy.py --config my.yaml   # 指定配置文件
python proxy.py --port 9000        # 覆盖端口
```

## 日志

日志文件在 `logs/proxy.log`，按日轮转，保留最近 7 天。

日志内容示例：
```
2026-04-26 10:05:12 [INFO] → https://api.deepseek.com/v1/chat/completions | provider=deepseek model=deepseek-chat stream=true
2026-04-26 10:05:12 [INFO] Override temperature: 0.8 → 0.7
2026-04-26 10:05:14 [INFO] ← Response completed
```

## 故障排查

| 现象 | 原因 | 解决 |
|------|------|------|
| `400 Unknown model` | model 未在路由表中，且未设置 default_provider | 在 `model_routing` 中添加映射 |
| `502 Bad Gateway` | 上游 API 连接失败 | 检查网络和 api_key |
| Cline 显示 `401 Unauthorized` | API Key 无效 | 检查环境变量或配置文件中的 key |
| `503 Provider not available` | 该 provider 的 api_key 未配置 | 在 config.yaml 中设置对应 api_key，或删除不需要的 provider |
| `Filtered out parameter` | 参数不在白名单中 | 将参数添加到 `allowed_params`（或确认该参数确实不需要） |
