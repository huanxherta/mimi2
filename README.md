# 小米AI Studio Claw 自动化控制系统

一个完整的网页控制面板，用于管理小米AI Studio Claw的自动化任务，包括环境重置、密钥管理、OpenAI协议中转等功能。

## 功能特性

### 🌐 网页控制面板
- 现代化的Web界面
- 实时状态监控
- 任务执行日志

### 👥 用户管理
- 批量导入小米凭证
- 自动识别多种凭证格式
- 用户切换和管理

### 🔄 自动化任务
- 一键环境重置
- SOUL模板恢复
- 环境变量备份

### 🔑 密钥管理
- 自动提取MIMO API密钥
- 密钥有效性验证
- 每 50 分钟经 MIMO API 校验 OC（401 则自动走 Claw 重取密钥）

### 🌉 OpenAI协议中转
- 兼容OpenAI API格式
- 自动密钥注入
- 支持多种模型映射

## 快速开始

### 1. 启动网页控制面板

**Windows:**
```bash
双击运行 start_web.bat
```

**Linux/Mac:**
```bash
python3 claw_web.py
```

访问: http://localhost:10060

同一端口 **10060** 提供 OpenAI 兼容接口，将客户端 `base_url` 设为 `http://localhost:10060/v1` 即可。后台每 50 分钟请求 MIMO `GET /v1/models` 校验 OC，若返回 **401** 则自动通过 Claw 重新获取密钥。

### 2. 导入小米凭证

在网页中找到"用户管理" -> "批量导入小米凭证"，支持以下格式：

#### CSV格式：
```
用户名,userId,serviceToken,xiaomichatbot_ph
示例用户,1234567890,/your-service-token-here...,your-xiaomichatbot-ph-here...
```

#### JSON格式：
```json
{"name":"示例用户", "userId":"1234567890", "serviceToken":"/your-service-token-here...", "xiaomichatbot_ph":"your-ph-here..."}
```

#### Netscape Cookie格式：
```
.xiaomimimo.com	TRUE	/	FALSE	1776696187	serviceToken	"/your-service-token-here..."
.xiaomimimo.com	TRUE	/	FALSE	1776724987	userId	1234567890
.xiaomimimo.com	TRUE	/	FALSE	1776696187	xiaomichatbot_ph	"your-ph-here..."
```

### 3. 执行环境重置

点击"启动环境重置"按钮，系统将：
- 连接到Claw
- 重置SOUL模板
- 备份环境变量
- 提取MIMO API密钥

### 4. 使用 OpenAI 兼容 API

获取密钥后，将客户端指向本机 `http://localhost:10060/v1`；直连小米云端可使用 `https://api.xiaomimimo.com`。

## API使用示例

```python
import openai

# 配置客户端
client = openai.OpenAI(
    api_key="your-mimo-key-here",
    base_url="https://api.xiaomimimo.com"
)

# 使用聊天完成
response = client.chat.completions.create(
    model="gpt-3.5-turbo",
    messages=[{"role": "user", "content": "Hello!"}]
)
```

## 文件说明

- `claw_web.py` - 主网页控制面板（默认端口 **10060**）
- `mimo_openai_shared.py` - OpenAI 中转共享常量与映射（供 `claw_web` / `claw_proxy` 共用）
- `claw_proxy.py` - 可选独立中转（默认 **8000**，一般无需使用；面板已内嵌 `/v1`）
- `claw_chat.py` - Claw 聊天客户端核心
- `claw_reset_env.py` - 环境重置自动化脚本
- `users/` - 面板导入的小米账号（`user_*.json`）与 `default.json`
- `app_state.json` - 当前全局 OC、体验到期等运行时状态（由面板写入）
- `oc_history.json` - 被替换/销毁的 OC 预览历史（若存在）
- `claw_users.json` - `claw_chat` 遗留用户表（与面板 `users/` 独立，一般可不编辑）
- `start_web.bat` - Windows 启动脚本
- `archive/` - **非运行时**归档（如 HTTP 抓包样本），详见 `archive/README.txt`

## 安全注意事项

- 凭证文件包含敏感信息，请妥善保管；`users/*.json`、`app_state.json` 等已列入 `.gitignore`，勿将真实数据提交到公开仓库
- 可选：复制 `env.example` 为 `.env` 并设置 `MIMO_RELAY_OPENAI_KEY`，为本地 `/v1` 中转配置独立 Bearer（不设则不对客户端 api_key 做校验，仅适合本机调试）
- 建议在本地网络环境中运行
- 定期更新小米凭证以避免过期

## 故障排除

### 连接失败
- 检查小米凭证是否有效
- 确认网络连接正常

### 密钥提取失败
- 确保Claw服务正常运行
- 检查用户权限

### 中转或校验失败
- 确认已获取有效的 MIMO API 密钥
- 检查本机 **10060** 是否被占用；若单独运行 `claw_proxy.py` 再检查 **8000**

## 更新日志

- v1.0: 初始版本，支持基础自动化功能
- v1.1: 添加网页控制面板和OpenAI中转
- v1.2: 改进凭证自动识别和密钥轮询
