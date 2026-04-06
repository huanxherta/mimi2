# Xiaomi AI Studio Claw Automation Control

高性能 Web 控制面板，用于小米 AI Studio Claw 自动化控制：环境重置、凭证管理、密钥管理、OpenAI 兼容中转。

## 功能

### Web 控制面板
- **FastAPI 高性能后端** — 异步全链路，支持高并发
- 现代深色 UI，侧边栏布局
- 实时状态监控
- 执行日志

### 用户管理
- 批量导入小米凭证
- 多种格式自动识别（Netscape Cookie / CSV / JSON）
- 用户切换与默认账号设置
- **多账号 OC 池** — 自动轮询负载均衡

### 自动化
- 一键环境重置
- SOUL 模板恢复
- 环境变量备份
- **自动 401 重试** — 密钥过期时自动切换到下一个有效密钥，并在后台刷新过期密钥

### 密钥管理
- 自动 MIMO API 密钥提取（通过 host-files API）
- 密钥有效性探测
- **后台密钥监控** — 每 5 分钟检查，超过 50 分钟主动刷新
- **多密钥轮询** — 支持多个小米账号，每个独立 OC 密钥，请求级轮询
- **三重保障** — 主动刷新 + 401 等待刷新 + 兜底重试

### OpenAI 兼容中转
- OpenAI API 兼容接口（`/v1/chat/completions`，`/v1/models`，`/v1/responses`）
- 自动密钥注入
- 模型名称映射
- 流式响应支持
- **Token 统计** — 记录输入/输出/缓存 Token 用量

## 快速开始

### 1. 安装依赖

```bash
pip install fastapi uvicorn httpx websocket-client requests
```

### 2. 运行

```bash
python claw_web_fast.py
```

如果 `python` 不可用，尝试 `python3` 或 Windows 下 `py -3`。

打开：http://localhost:10060

端口 **10060** 同时提供 OpenAI 兼容 API：设置客户端 `base_url` 为 `http://localhost:10060/v1`。

### 3. 导入小米凭证

在 UI 中：**用户管理** → **批量导入**。支持格式：

#### Netscape Cookie 格式

```
# domain  flag  path  secure  expiry  name  value
.xiaomimimo.com	TRUE	/	TRUE	0	serviceToken	your-service-token
.xiaomimimo.com	TRUE	/	TRUE	0	userId	your-user-id
.xiaomimimo.com	TRUE	/	TRUE	0	xiaomichatbot_ph	your-ph
```

#### CSV 格式

```
name,userId,serviceToken,xiaomichatbot_ph
账号1,1234567890,/your-service-token...,your-ph...
```

#### JSON 格式

```json
{
  "name": "Account1",
  "userId": "1234567890",
  "serviceToken": "/your-service-token...",
  "xiaomichatbot_ph": "your-ph..."
}
```

### 4. 获取 OC 密钥

点击「刷新 OC」，系统会自动：
1. 连接 Claw WebSocket
2. 重置 SOUL 配置
3. 备份环境变量
4. 通过 HTTP API 下载并提取 MIMO_API_KEY
5. 探测密钥有效性
6. 失败时自动销毁重来（最多 3 次，可通过面板调整）

### 5. 配置中转鉴权（可选）

创建 `.env` 文件：

```
MIMO_RELAY_OPENAI_KEY=sk-your-key-here
```

设置后，OpenAI 中转接口需要 Bearer Token 认证。未设置时为开放访问。

## 文件结构

```
├── claw_web_fast.py    # FastAPI 主入口 + 路由
├── web_core.py         # 核心服务逻辑
├── claw_chat.py        # Claw WebSocket 客户端
├── claw_reset_env.py   # 环境重置逻辑
├── mimo_openai_shared.py  # OpenAI 共享模块
├── mimi2_responses.py  # OpenAI Responses API 兼容层
├── templates/
│   └── index.html      # HTML 模板
├── static/
│   ├── style.css       # 样式
│   └── app.js          # 前端逻辑
├── users/              # 用户凭证文件
├── app_state.json      # 应用状态
├── oc_history.json     # OC 历史
└── .env                # 环境变量（可选）
```

## API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/v1/chat/completions` | POST | OpenAI 兼容聊天 |
| `/v1/responses` | POST | OpenAI Responses API |
| `/v1/models` | GET | 模型列表 |
| `/api/status` | GET | 系统状态 |
| `/api/accounts_health` | GET | 账号健康检测 |
| `/api/oc_catalog` | GET | OC 目录 |
| `/api/token_stats` | GET | Token 统计 |
| `/api/manual_refresh` | POST | 手动刷新 OC |
| `/api/destroy_claw` | POST | 销毁 Claw 实例 |

## License

MIT
