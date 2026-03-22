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

### Automation
- One-click environment reset
- SOUL template restore
- Environment variable backup

### Key management
- Automatic MIMO API key extraction
- Key validation
- Every ~50 minutes the app calls the MIMO API to validate the OC key; on **401** it refreshes the key via Claw

### OpenAI-compatible relay
- OpenAI API–compatible surface
- Automatic key injection for upstream MIMO
- Model name mapping

## Quick start

### 1. Run the web panel

From the project directory:

```bash
python claw_web.py
```

If `python` is not available, try `python3` or on Windows `py -3`.

Open: http://localhost:10060

The same port **10060** exposes an OpenAI-compatible API: set the client `base_url` to `http://localhost:10060/v1`. In the background, `GET /v1/models` runs about every 50 minutes to validate the OC; on **401**, Claw is used to fetch a new key.

### 2. Import Xiaomi credentials

In the UI: **User management** → **Bulk import**. Supported formats:

#### CSV

```
name,userId,serviceToken,xiaomichatbot_ph
Example,1234567890,/your-service-token-here...,your-xiaomichatbot-ph-here...
```

#### JSON

```json
{"name":"Example", "userId":"1234567890", "serviceToken":"/your-service-token-here...", "xiaomichatbot_ph":"your-ph-here..."}
```

#### Netscape cookie file

```
.xiaomimimo.com	TRUE	/	FALSE	1776696187	serviceToken	"/your-service-token-here..."
.xiaomimimo.com	TRUE	/	FALSE	1776724987	userId	1234567890
.xiaomimimo.com	TRUE	/	FALSE	1776696187	xiaomichatbot_ph	"your-ph-here..."
```

### 3. Environment reset

Click **Start environment reset**. The app will:
- Connect to Claw
- Reset the SOUL template
- Back up environment variables
- Extract the MIMO API key

### 4. OpenAI-compatible API

After you have a key, point the client at `http://localhost:10060/v1` for the local relay. For direct access to Xiaomi’s cloud, use `https://api.xiaomimimo.com`.

## API example

```python
import openai

client = openai.OpenAI(
    api_key="your-mimo-key-here",
    base_url="https://api.xiaomimimo.com"
)

response = client.chat.completions.create(
    model="gpt-3.5-turbo",
    messages=[{"role": "user", "content": "Hello!"}]
)
```

## File layout

- `claw_web.py` — Main web panel (default port **10060**)
- `mimo_openai_shared.py` — Shared relay constants and model mapping (used by `claw_web` and `claw_proxy`)
- `claw_proxy.py` — Optional standalone relay (default **8000**; usually unnecessary because the panel embeds `/v1`)
- `claw_chat.py` — Claw chat client core
- `claw_reset_env.py` — Environment reset automation
- `users/` — Imported Xiaomi accounts (`user_*.json`, `default.json`)
- `app_state.json` — Runtime state (global OC, trial expiry, etc.), written by the panel
- `oc_history.json` — History of replaced/revoked OC previews (if present)
- `claw_users.json` — Legacy user table for `claw_chat` (separate from `users/`)
- `archive/` — Non-runtime archive (e.g. HTTP capture notes); see `archive/README.txt`

## Security

- Credential files are sensitive; `users/*.json`, `app_state.json`, etc. are listed in `.gitignore` — do not commit real data to a public repo.
- Optional: copy `env.example` to `.env` and set `MIMO_RELAY_OPENAI_KEY` to a random `sk-...` value so the local `/v1` relay requires a Bearer token. If unset, the relay does not validate the client `api_key` (convenient for local debugging only).
- Prefer running on a trusted local network.
- Refresh Xiaomi credentials before they expire.

## Troubleshooting

### Connection failures
- Verify Xiaomi credentials are still valid
- Check network connectivity

### Key extraction fails
- Ensure the Claw service is running
- Check account permissions

### Relay or validation errors
- Confirm a valid MIMO API key is available
- Ensure port **10060** is free; if you run `claw_proxy.py` alone, check **8000**

## Changelog

- v1.0: Initial release with basic automation
- v1.1: Web panel and OpenAI relay
- v1.2: Improved credential parsing and key refresh
