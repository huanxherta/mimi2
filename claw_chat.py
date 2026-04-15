#!/usr/bin/env python3
"""
小米 AI Studio Claw 聊天客户端 (纯异步重构版)
通过 WebSocket 与 Mimo Claw 对话、操作文件
依赖: pip install httpx websockets
"""
import os
import json
import sys
import time
import uuid
import asyncio
from urllib.parse import quote

import httpx
import websockets

# ========== 配置 ==========
BASE_URL = "https://aistudio.xiaomimimo.com"
WS_URL = "wss://aistudio.xiaomimimo.com/ws/proxy"
USERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "claw_users.json")

# 全局变量，由 load_user() 设置
PH = ""
COOKIES = {}


def _aistudio_cors_json_headers():
    return {
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/",
        "x-timezone": "Asia/Shanghai",
    }


def aistudio_session_401_hint():
    return (
        "aistudio 会话 401：serviceToken 已失效或未登录。"
        "请用浏览器登录小米账号并打开 aistudio.xiaomimimo.com，"
        "重新导出 Cookie 后在面板「批量导入」更新该账号凭证。"
    )


async def wait_mimo_claw_available(timeout_sec=120, poll_interval=2, cookies=None):
    url = f"{BASE_URL}/open-apis/user/mimo-claw/status"
    deadline = time.time() + timeout_sec
    last_printed = None
    _cookies = cookies or COOKIES
    
    async with httpx.AsyncClient() as client:
        while time.time() < deadline:
            r = await client.get(
                url,
                cookies=_cookies,
                headers=_aistudio_cors_json_headers(),
                timeout=15,
            )
            if r.status_code == 401:
                print(f"[!] {aistudio_session_401_hint()}", file=sys.stderr)
                return False
            try:
                d = r.json()
            except Exception:
                await asyncio.sleep(poll_interval)
                continue
            if d.get("code") != 0:
                await asyncio.sleep(poll_interval)
                continue
            data = d.get("data") or {}
            st = (data.get("status") or "").strip()
            if st and st != last_printed:
                print(f"[*] mimo-claw/status: {st}", file=sys.stderr)
                last_printed = st
            if st == "AVAILABLE":
                return True
            if st in ("FAILED", "DESTROYED", "ERROR"):
                print(f"[!] Claw 状态异常: {st}", file=sys.stderr)
                return False
            await asyncio.sleep(poll_interval)
            
    print("[!] 等待 Claw AVAILABLE 超时，请稍后重试或查看 Studio 控制台", file=sys.stderr)
    return False


async def _post_agreement_mimo_claw(ph=None, cookies=None):
    _ph = ph or PH
    _cookies = cookies or COOKIES
    url = f"{BASE_URL}/open-apis/agreement/user/mimo-claw?xiaomichatbot_ph={quote(_ph)}"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                url,
                cookies=_cookies,
                headers=_aistudio_cors_json_headers(),
                timeout=15,
            )
            if r.status_code == 401:
                print(f"[*] agreement 401（会话失效）: {aistudio_session_401_hint()}", file=sys.stderr)
                return
            d = r.json()
            c = d.get("code")
            if c == 2007:
                print("[*] agreement 返回 2007，仍继续 create", file=sys.stderr)
            elif c != 0:
                print(f"[*] agreement/mimo-claw: {d}", file=sys.stderr)
    except Exception as e:
        print(f"[*] agreement/mimo-claw 请求: {e}", file=sys.stderr)


def load_user(user_id=None):
    global PH, COOKIES
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    
    uid = user_id or cfg.get("default", "1")
    users = cfg.get("users", {})
    if uid not in users:
        print(f"用户 {uid} 不存在，可用: {', '.join(users.keys())}", file=sys.stderr)
        sys.exit(1)
    
    user = users[uid]
    PH = user["xiaomichatbot_ph"]
    COOKIES = {
        "serviceToken": user["serviceToken"],
        "userId": user["userId"],
        "xiaomichatbot_ph": PH,
    }
    print(f"已加载用户: {user.get('name', uid)} ({user['userId']})", file=sys.stderr)
    return user


def list_users():
    with open(USERS_FILE, "r") as f:
        cfg = json.load(f)
    default = cfg.get("default", "1")
    for uid, user in cfg.get("users", {}).items():
        marker = " (默认)" if uid == default else ""
        print(f"  [{uid}] {user.get('name', '未知')} — {user['userId']}{marker}")


def add_user(name, user_id, serviceToken, xiaomichatbot_ph, set_default=False):
    with open(USERS_FILE, "r") as f:
        cfg = json.load(f)
    existing = set(cfg.get("users", {}).keys())
    uid = str(max(int(k) for k in existing) + 1) if existing else "1"
    
    cfg["users"][uid] = {
        "name": name,
        "userId": user_id,
        "serviceToken": serviceToken,
        "xiaomichatbot_ph": xiaomichatbot_ph,
    }
    if set_default:
        cfg["default"] = uid
    with open(USERS_FILE, "w") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)
    print(f"已添加用户 [{uid}] {name} ({user_id})", file=sys.stderr)
    return uid


async def get_ticket(ph=None, cookies=None):
    _ph = ph or PH
    _cookies = cookies or COOKIES
    url = f"{BASE_URL}/open-apis/user/ws/ticket?xiaomichatbot_ph={quote(_ph)}"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, cookies=_cookies, headers=_aistudio_cors_json_headers(), timeout=15)
        if r.status_code == 401:
            raise Exception(aistudio_session_401_hint())
        if r.status_code != 200:
            raise Exception(f"HTTP {r.status_code}: {r.text}")
        d = r.json()
        if "data" not in d or "ticket" not in d["data"]:
            raise Exception(f"Unexpected response: {d}")
        return d["data"]["ticket"]


class ClawClient:
    def __init__(self, ph=None, cookies=None):
        self.ws = None
        self.connected = False
        self.responses = {}
        self.events = []
        self.session_key = "agent:main:main"
        self.agent_id = "main"
        self._ph = ph or PH
        self._cookies = dict(cookies) if cookies else dict(COOKIES)
        self._http = httpx.AsyncClient(timeout=120)
        self._listen_task = None
        
    async def _create_claw(self):
        await _post_agreement_mimo_claw(self._ph, self._cookies)
        url = f"{BASE_URL}/open-apis/user/mimo-claw/create?xiaomichatbot_ph={quote(self._ph)}"
        r = await self._http.post(url, cookies=self._cookies, headers=_aistudio_cors_json_headers(), timeout=15)
        if r.status_code == 401:
            print(f"[!] mimo-claw/create 401: {aistudio_session_401_hint()}", file=sys.stderr)
            return False
        d = r.json()
        if d.get("code") == 0:
            status = d.get("data", {}).get("status", "")
            print(f"[+] Claw 状态: {status}", file=sys.stderr)
            return True
        print(f"[!] 创建失败: {d}", file=sys.stderr)
        return False

    async def connect(self, auto_create=True):
        try:
            ticket = await get_ticket(ph=self._ph, cookies=self._cookies)
        except Exception as e:
            if not auto_create:
                raise
            es = str(e)
            if "aistudio 会话 401" in es:
                print(f"[!] {es}", file=sys.stderr)
                return False
            print("[*] ticket 获取失败，尝试创建 Claw...", file=sys.stderr)
            if not await self._create_claw():
                return False
            if not await wait_mimo_claw_available(cookies=self._cookies):
                return False
            try:
                ticket = await get_ticket(ph=self._ph, cookies=self._cookies)
            except Exception as e2:
                print(f"[!] 创建后轮询就绪后仍无法获取 ticket: {e2}", file=sys.stderr)
                return False

        cookie_str = "; ".join(f'{k}="{v}"' if ' ' in v or '=' in v else f'{k}={v}' for k, v in self._cookies.items())
        headers_dict = {"Cookie": cookie_str, "Origin": BASE_URL}
        
        try:
            try:
                # 优先尝试 websockets >= 14.0 的新版 API 参数
                self.ws = await websockets.connect(
                    f"{WS_URL}?ticket={ticket}",
                    additional_headers=headers_dict
                )
            except TypeError as e:
                # 如果抛出了未预期的 additional_headers 参数，说明你用的是旧版本 (<14.0)
                if "additional_headers" in str(e):
                    self.ws = await websockets.connect(
                        f"{WS_URL}?ticket={ticket}",
                        extra_headers=headers_dict
                    )
                else:
                    raise
        except Exception as e:
            print(f"WS Connect Error: {e}", file=sys.stderr)
            return False

        self.connected = False
        self._listen_task = asyncio.create_task(self._ws_loop())
        
        for _ in range(50):
            if self.connected: return True
            await asyncio.sleep(0.1)
        return False
    
    async def _ws_loop(self):
        try:
            async for message in self.ws:
                data = json.loads(message)
                if data["type"] == "event" and data.get("event") == "connect.challenge":
                    await self.ws.send(json.dumps({
                        "type": "req", "id": str(uuid.uuid4()), "method": "connect",
                        "params": {
                            "minProtocol": 3, "maxProtocol": 3,
                            "client": {"id": "cli", "version": "mimo-claw-ui", "platform": "Linux x86_64", "mode": "cli"},
                            "role": "operator",
                            "scopes": ["operator.admin", "operator.read", "operator.write", "operator.approvals", "operator.pairing"],
                            "caps": ["tool-events"],
                            "userAgent": "Mozilla/5.0", "locale": "zh-CN"
                        }
                    }))
                elif data["type"] == "res":
                    self.responses[data["id"]] = data
                    if data.get("ok") and data.get("payload", {}).get("type") == "hello-ok":
                        self.connected = True
                elif data["type"] == "event":
                    self.events.append(data)
        except Exception:
            self.connected = False
    
    async def _safe_send(self, payload):
        if not self.connected or not self.ws:
            if not await self.connect():
                raise Exception("WebSocket 未连接且重连失败")
        try:
            await self.ws.send(json.dumps(payload))
        except Exception as e:
            print(f"[WARN] 发送失败，尝试重连: {e}", file=sys.stderr)
            self.connected = False
            await self.close()
            if not await self.connect():
                raise
            await self.ws.send(json.dumps(payload))

    async def _request(self, method, params=None, timeout=30):
        req_id = str(uuid.uuid4())
        try:
            await self._safe_send({"type": "req", "id": req_id, "method": method, "params": params or {}})
        except Exception as e:
            print(f"[ERROR] _request 发送失败: {e}", file=sys.stderr)
            return None

        for _ in range(timeout * 10):
            if req_id in self.responses:
                return self.responses.pop(req_id)
            await asyncio.sleep(0.1)
        return None
    
    async def send_message(self, text, timeout=60):
        self.events.clear()
        payload = {
            "type": "req", "id": str(uuid.uuid4()), "method": "chat.send",
            "params": {"sessionKey": self.session_key, "message": text, "idempotencyKey": str(uuid.uuid4())}
        }
        try:
            await self._safe_send(payload)
        except Exception as e:
            return f"(发送失败: {e})"

        reply = None
        for _ in range(timeout * 10):
            for evt in self.events:
                if evt.get("event") == "chat":
                    msg = evt.get("payload", {}).get("message", {})
                    if msg.get("role") == "assistant":
                        for c in msg.get("content", []):
                            if c.get("type") == "text" and c.get("text"):
                                reply = c["text"]
                    if evt.get("payload", {}).get("state") == "final" and reply:
                        self.events.clear()
                        return reply
            await asyncio.sleep(0.1)
        self.events.clear()
        return reply or "(无回复)"
    
    async def get_history(self, limit=20):
        res = await self._request("chat.history", {"sessionKey": self.session_key, "limit": limit})
        if res and res.get("ok"):
            return res["payload"].get("messages", [])
        return []
    
    async def list_sessions(self):
        res = await self._request("sessions.list", {"includeGlobal": True, "limit": 120})
        if res and res.get("ok"):
            return res["payload"].get("sessions", [])
        return []
    
    async def list_files(self):
        res = await self._request("agents.files.list", {"agentId": self.agent_id})
        if res and res.get("ok"):
            return res["payload"].get("files", [])
        return []
    
    async def read_file(self, name):
        res = await self._request("agents.files.get", {"agentId": self.agent_id, "name": name})
        if res and res.get("ok"):
            return res["payload"].get("file", {}).get("content", "")
        return None
    
    async def download_file(self, name, save_to=None):
        content = await self.read_file(name)
        if content is not None:
            save_path = save_to or name
            with open(save_path, "w") as f:
                f.write(content)
            print(f"已保存到: {save_path}", file=sys.stderr)
            return True
        return False
    
    async def http_list_files(self, path="/root/.openclaw/workspace"):
        url = f"{BASE_URL}/open-apis/host-files/list"
        headers = {"Content-Type": "application/json", "Origin": BASE_URL, "Referer": f"{BASE_URL}/"}
        r = await self._http.get(url, cookies=self._cookies, headers=headers, params={"path": path, "xiaomichatbot_ph": self._ph}, timeout=15)
        d = r.json()
        if d.get("code") == 0:
            return d["data"].get("items", [])
        return None

    async def http_chat_conversation_list(self, page_num=1, page_size=20):
        url = f"{BASE_URL}/open-apis/chat/conversation/list"
        headers = {"Content-Type": "application/json", "Origin": BASE_URL, "Referer": f"{BASE_URL}/"}
        r = await self._http.post(url, cookies=COOKIES, headers=headers, params={"xiaomichatbot_ph": PH}, json={"pageInfo": {"pageNum": page_num, "pageSize": page_size}}, timeout=15)
        d = r.json()
        if d.get("code") == 0:
            return d.get("data")
        return None
    
    async def http_download_file(self, path, save_to=None):
        url = f"{BASE_URL}/open-apis/host-files/download?xiaomichatbot_ph={quote(PH)}"
        headers = {"Content-Type": "application/json", "Origin": BASE_URL, "Referer": f"{BASE_URL}/"}
        r = await self._http.post(url, cookies=COOKIES, headers=headers, json={"path": path}, timeout=15)
        d = r.json()
        if d.get("code") != 0: return None
        resource_url = d["data"].get("resourceUrl")
        if not resource_url: return None
        resp = await self._http.get(resource_url, timeout=30)
        if resp.status_code == 200:
            save_path = save_to or path.split("/")[-1]
            with open(save_path, "w") as f:
                f.write(resp.text)
            print(f"已保存到: {save_path}", file=sys.stderr)
            return resp.text
        return None
    
    async def close(self):
        self.connected = False
        if self._listen_task:
            self._listen_task.cancel()
        if self.ws:
            await self.ws.close()
        await self._http.aclose()


async def chat_interactive(client):
    print("交互式聊天 (quit=退出, /history=历史, /files=文件, /read <name>=读取, /download <name> [save]=下载)")
    print("-" * 60)
    while True:
        try:
            msg = await asyncio.to_thread(input, "\n你: ")
            msg = msg.strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not msg: continue
        if msg.lower() in ("quit", "exit", "q"): break
        
        if msg == "/history":
            for m in await client.get_history(10):
                role = "你" if m["role"] == "user" else "Claw"
                for c in m.get("content", []):
                    if c.get("type") == "text": print(f"  [{role}] {c['text'][:100]}")
            continue
        if msg == "/files":
            for f in await client.list_files():
                print(f"  📄 {f['name']}  ({f.get('size', 0)} bytes)")
            continue
        if msg.startswith("/read "):
            name = msg[6:].strip()
            content = await client.read_file(name)
            if content is not None:
                print(f"\n--- {name} ---\n{content[:2000]}")
            else:
                print(f"  读取失败: {name}")
            continue
        if msg.startswith("/download "):
            parts = msg.split(" ", 2)
            name = parts[1]
            save_to = parts[2] if len(parts) > 2 else name
            if await client.download_file(name, save_to):
                print(f"  ✅ 已下载")
            else:
                print(f"  ❌ 下载失败")
            continue
        
        print("Claw: ", end="", flush=True)
        print(await client.send_message(msg))


async def async_main():
    if len(sys.argv) < 2:
        print("用法: python3 claw_chat.py <命令> [参数]")
        print("\n命令:\n  chat\n  send <消息>\n  history\n  sessions\n  conversations\n  files\n  ls [path]\n  read <文件名>\n  download <路径> [保存路径]\n  create\n  users\n  add-user <name> <uid> <token>")
        sys.exit(1)
    
    cmd = sys.argv[1]
    if cmd == "users":
        list_users()
        return
    if cmd == "add-user":
        if len(sys.argv) < 5:
            print("用法: claw_chat.py add-user <name> <userId> <serviceToken> [ph]", file=sys.stderr)
            sys.exit(1)
        ph = sys.argv[5] if len(sys.argv) > 5 else "kHbEyClURiAkISDYkZ2reQ=="
        add_user(sys.argv[2], sys.argv[3], sys.argv[4], ph)
        return
    
    load_user(os.environ.get("CLAW_USER"))
    print("正在连接 Claw...", file=sys.stderr)
    client = ClawClient()
    if not await client.connect():
        print("连接失败!", file=sys.stderr)
        sys.exit(1)
    print("已连接!", file=sys.stderr)
    
    try:
        if cmd == "chat":
            await chat_interactive(client)
        elif cmd == "send":
            print(await client.send_message(" ".join(sys.argv[2:])))
        elif cmd == "history":
            for m in await client.get_history(20):
                role = "你" if m["role"] == "user" else "Claw"
                for c in m.get("content", []):
                    if c.get("type") == "text": print(f"[{role}] {c['text'][:150]}")
        elif cmd == "sessions":
            for s in await client.list_sessions():
                print(f"  {s['key']}  ({s.get('kind', '?')})")
        elif cmd == "conversations":
            data = await client.http_chat_conversation_list()
            if data is not None:
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                print("会话列表获取失败", file=sys.stderr)
        elif cmd == "files":
            for f in await client.list_files():
                print(f"📄 {f['name']}  ({f.get('size', 0)} bytes)")
        elif cmd == "ls":
            path = sys.argv[2] if len(sys.argv) > 2 else "/root/.openclaw/workspace"
            items = await client.http_list_files(path)
            if items is not None:
                print(f"\U0001f4c1 {path} ({len(items)} 项):")
                for item in items:
                    kind = "\U0001f4c1" if item.get("directory") else "\U0001f4c4"
                    print(f"  {kind} {item['name']}  ({item.get('size', 0)} bytes)")
            else:
                print(f"(API 不支持 {path}，尝试 Claw...)", file=sys.stderr)
                print(await client.send_message(f"执行 ls -la {path}，以表格形式返回：权限 用户 大小 日期 文件名。只返回数据。"))
        elif cmd == "read":
            content = await client.read_file(sys.argv[2])
            print(content if content is not None else f"读取失败: {sys.argv[2]}")
        elif cmd == "create":
            await _post_agreement_mimo_claw()
            async with httpx.AsyncClient() as hc:
                r = await hc.post(
                    f"{BASE_URL}/open-apis/user/mimo-claw/create?xiaomichatbot_ph={quote(PH)}",
                    cookies=COOKIES, headers=_aistudio_cors_json_headers(), timeout=30
                )
                d = r.json()
                if d["code"] == 0:
                    s = d["data"]
                    import datetime
                    exp = datetime.datetime.fromtimestamp(s["expireTime"]/1000).strftime("%Y-%m-%d %H:%M")
                    print(f"状态: {s['status']} — {s['message']}\n过期: {exp}")
                else:
                    print(f"失败: {d['msg']}")
        elif cmd == "download":
            name = sys.argv[2]
            save_to = sys.argv[3] if len(sys.argv) > 3 else name.split("/")[-1]
            dl_path = name if name.startswith("/") else f"/root/.openclaw/workspace/{name}"
            if not await client.http_download_file(dl_path, save_to):
                if not await client.download_file(name, save_to):
                    print(f"下载失败: {name}", file=sys.stderr)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(async_main())