# Xiaomi AI Studio Claw Automation Control

A web control panel for Xiaomi AI Studio Claw automation: environment reset, credential and key management, and an OpenAI-compatible relay.

## Features

### Web control panel
- Modern web UI
- Live status
- Task logs

### User management
- Bulk import of Xiaomi credentials
- Multiple credential formats (auto-detected)
- User switching
- **Multi-account OC pool** with automatic round-robin load balancing

### Automation
- One-click environment reset
- SOUL template restore
- Environment variable backup
- **Automatic 401 retry** — on key expiry, transparently switch to next valid key and refresh the expired one in background

### Key management
- Automatic MIMO API key extraction
- Key validation via chat API probing
- **Background key monitor** — every ~50 minutes validates all OC keys; invalid ones are auto-refreshed via Claw
- **Multi-key rotation** — supports multiple Xiaomi accounts, each with its own OC key, rotated per-request

### OpenAI-compatible relay
- OpenAI API–compatible surface (`/v1/chat/completions`, `/v1/models`)
- Automatic key injection for upstream MIMO
- Model name mapping
- Streaming support

## Quick start

### 1. Run the web panel

From the project directory:

```bash
python claw_web.py
```

If `python` is not available, try `python3` or on Windows `py -3`.

Open: http://localhost:10060

The same port **10060** exposes an OpenAI-compatible API: set the client `base_url` to `http://localhost:10060/v1`.

### 2. Import Xiaomi credentials

In the UI: **User management** → **Bulk import**. Supported formats:

#### CSV

```
name,userId,serviceToken,xiaomichatbot_ph
Example,1234567890,/your-service-token-here...,your-xiaomichatbot-ph-here...
```

#### JSON

```json
{\