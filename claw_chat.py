#!/usr/bin/env python3
"""
小米 AI Studio Claw 聊天客户端
通过 WebSocket 与 Mimo Claw 对话、操作文件
"""
import os
import json
import sys
import time
import uuid
import requests
import websocket
import threading
from urllib.parse import quote

# ========== 配置 ==========
BASE_URL = "https://aistudio.xiaomimimo.com"
WS_URL = "wss://aistudio.xiaomimimo.com/ws/proxy"
USERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "claw_users.json")

# 全局变量，由 load_user() 设置
PH = ""
COOKIES = {}


def _aistudio_cors_json_headers():
    """与 aistudio 抓包一致：CORS + JSON Content-Type + x-timezone。"""
    return {
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/",
        "x-timezone": "Asia/Shanghai",
    }


def aistudio_session_401_hint():
    """HTTP 401 + loginUrl 时的统一说明（与面板导入的 serviceToken 绑定）。"""
    return (
        "aistudio 会话 401：serviceToken 已失效或未登录。"
        "请用浏览器登录小米账号并打开 aistudio.xiaomimimo.com，"
        "重新导出 Cookie 后在面板「批量导入」更新该账号凭证。"
    )


def wait_mimo_claw_available(timeout_sec=120, poll_interval=2):
    """
    创建后轮询 GET mimo-claw/status，直到 data.status == AVAILABLE（见「创建1」抓包：多次 CREATING 后才 ticket）。
    固定 sleep(3) 往往过短，会导致后续 ws/ticket 在实例未就绪时失败。
    """
    url = f"{BASE_URL}/open-apis/user/mimo-claw/status"
    deadline = time.time() + timeout_sec
    last_printed = None
    while time.time() < deadline:
        r = requests.get(
            url,
            cookies=COOKIES,
            headers=_aistudio_cors_json_headers(),
            timeout=15,
        )
        if r.status_code == 401:
            print(f"[!] {aistudio_session_401_hint()}", file=sys.stderr)
            return False
        try:
            d = r.json()
        except json.JSONDecodeError:
            time.sleep(poll_interval)
            continue
        if d.get("code") != 0:
            time.sleep(poll_interval)
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
        time.sleep(poll_interval)
    print("[!] 等待 Claw AVAILABLE 超时，请稍后重试或查看 Studio 控制台", file=sys.stderr)
    return False


def _post_agreement_mimo_claw():
    """
    抓包 mimo/[437]：在 mimo-claw/create 之前 POST /open-apis/agreement/user/mimo-claw（空 body）。
    失败不阻断后续 create，仅打日志。
    """
    url = f"{BASE_URL}/open-apis/agreement/user/mimo-claw?xiaomichatbot_ph={quote(PH)}"
    try:
        r = requests.post(
            url,
            cookies=COOKIES,
            headers=_aistudio_cors_json_headers(),
            timeout=15,
        )
        if r.status_code == 401:
            print(f"[*] agreement 401（会话失效）: {aistudio_session_401_hint()}", file=sys.stderr)
            return
        d = r.json()
        c = d.get("code")
        if c == 2007:
            print(
                "[*] agreement 返回 2007（法务免责声明更新失败），仍继续 create（与浏览器抓包一致）",
                file=sys.stderr,
            )
        elif c != 0:
            print(f"[*] agreement/mimo-claw: {d}", file=sys.stderr)
    except Exception as e:
        print(f"[*] agreement/mimo-claw 请求: {e}", file=sys.stderr)


def load_user(user_id=None):
    """加载用户凭证"""
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
    """列出所有用户"""
    with open(USERS_FILE, "r") as f:
        cfg = json.load(f)
    default = cfg.get("default", "1")
    for uid, user in cfg.get("users", {}).items():
        marker = " (默认)" if uid == default else ""
        print(f"  [{uid}] {user.get('name', '未知')} — {user['userId']}{marker}")


def add_user(name, user_id, serviceToken, xiaomichatbot_ph, set_default=False):
    """添加新用户"""
    with open(USERS_FILE, "r") as f:
        cfg = json.load(f)
    
    # 找下一个可用 ID
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


def get_ticket():
    url = f"{BASE_URL}/open-apis/user/ws/ticket?xiaomichatbot_ph={quote(PH)}"
    r = requests.get(
        url,
        cookies=COOKIES,
        headers=_aistudio_cors_json_headers(),
        timeout=15,
    )
    if r.status_code == 401:
        raise Exception(aistudio_session_401_hint())
    if r.status_code != 200:
        raise Exception(f"HTTP {r.status_code}: {r.text}")
    d = r.json()
    if "data" not in d or "ticket" not in d["data"]:
        raise Exception(f"Unexpected response: {d}")
    return d["data"]["ticket"]


class ClawClient:
    def __init__(self):
        self.ws = None
        self.connected = False
        self.responses = {}
        self.events = []
        self.session_key = "agent:main:main"
        self.agent_id = "main"
        
    def _create_claw(self):
        """创建/续期 Claw 实例。须 POST（抓包 mimo/[438]，空 body）；浏览器地址栏打开同 URL 会发 GET→405。"""
        _post_agreement_mimo_claw()
        url = f"{BASE_URL}/open-apis/user/mimo-claw/create?xiaomichatbot_ph={quote(PH)}"
        r = requests.post(
            url,
            cookies=COOKIES,
            headers=_aistudio_cors_json_headers(),
            timeout=15,
        )
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

    def connect(self, auto_create=True):
        try:
            ticket = get_ticket()
        except Exception as e:
            if not auto_create:
                raise
            es = str(e)
            if "aistudio 会话 401" in es:
                print(f"[!] {es}", file=sys.stderr)
                return False
            print("[*] ticket 获取失败，尝试创建 Claw...", file=sys.stderr)
            if not self._create_claw():
                return False
            if not wait_mimo_claw_available():
                return False
            try:
                ticket = get_ticket()
            except Exception as e2:
                print(f"[!] 创建后轮询就绪后仍无法获取 ticket: {e2}", file=sys.stderr)
                return False
        cookie_str = "; ".join(f'{k}="{v}"' if ' ' in v or '=' in v else f'{k}={v}' for k, v in COOKIES.items())
        self.ws = websocket.WebSocketApp(
            f"{WS_URL}?ticket={ticket}",
            header=[f"Cookie: {cookie_str}", "Origin: https://aistudio.xiaomimimo.com"],
            on_message=self._on_message,
            on_error=lambda ws, e: print(f"WS Error: {e}", file=sys.stderr),
            on_close=lambda ws, c, m: setattr(self, 'connected', False),
        )
        t = threading.Thread(target=self.ws.run_forever, kwargs={"sslopt": {"cert_reqs": 0}})
        t.daemon = True
        t.start()
        for _ in range(50):
            if self.connected: return True
            time.sleep(0.1)
        return False
    
    def _on_message(self, ws, message):
        data = json.loads(message)
        if data["type"] == "event" and data.get("event") == "connect.challenge":
            ws.send(json.dumps({
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
    
    def _safe_send(self, payload):
        if not self.connected or not self.ws:
            if not self.connect():
                raise Exception("WebSocket 未连接且重连失败")

        try:
            self.ws.send(json.dumps(payload))
        except websocket.WebSocketConnectionClosedException as e:
            print(f"[WARN] WebSocket 已关闭，尝试重连: {e}", file=sys.stderr)
            self.connected = False
            self.close()
            if not self.connect():
                raise
            self.ws.send(json.dumps(payload))
        except Exception as e:
            print(f"[ERROR] 发送数据失败: {e}", file=sys.stderr)
            self.connected = False
            self.close()
            raise

    def _request(self, method, params=None, timeout=30):
        req_id = str(uuid.uuid4())
        try:
            self._safe_send({"type": "req", "id": req_id, "method": method, "params": params or {}})
        except Exception as e:
            print(f"[ERROR] _request 发送失败: {e}", file=sys.stderr)
            return None

        for _ in range(timeout * 10):
            if req_id in self.responses:
                return self.responses.pop(req_id)
            time.sleep(0.1)
        return None
    
    def send_message(self, text, timeout=60):
        self.events.clear()
        payload = {
            "type": "req",
            "id": str(uuid.uuid4()),
            "method": "chat.send",
            "params": {
                "sessionKey": self.session_key,
                "message": text,
                "idempotencyKey": str(uuid.uuid4())
            }
        }

        try:
            self._safe_send(payload)
        except Exception as e:
            print(f"[ERROR] send_message 发送失败: {e}", file=sys.stderr)
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
            time.sleep(0.1)

        self.events.clear()
        return reply or "(无回复)"
    
    def get_history(self, limit=20):
        res = self._request("chat.history", {"sessionKey": self.session_key, "limit": limit})
        if res and res.get("ok"):
            return res["payload"].get("messages", [])
        return []
    
    def list_sessions(self):
        res = self._request("sessions.list", {"includeGlobal": True, "limit": 120})
        if res and res.get("ok"):
            return res["payload"].get("sessions", [])
        return []
    
    def list_files(self):
        """通过 agents.files.list 列出 agent 工作区文件"""
        res = self._request("agents.files.list", {"agentId": self.agent_id})
        if res and res.get("ok"):
            return res["payload"].get("files", [])
        return []
    
    def read_file(self, name):
        """通过 agents.files.get 读取文件内容"""
        res = self._request("agents.files.get", {"agentId": self.agent_id, "name": name})
        if res and res.get("ok"):
            file_info = res["payload"].get("file", {})
            return file_info.get("content", "")
        return None
    
    def download_file(self, name, save_to=None):
        content = self.read_file(name)
        if content is not None:
            save_path = save_to or name
            with open(save_path, "w") as f:
                f.write(content)
            print(f"已保存到: {save_path}", file=sys.stderr)
            return True
        return False
    
    def http_list_files(self, path="/root/.openclaw/workspace"):
        """通过 HTTP API 列出主机工作区文件（含 env 等）；query 带当前账号 xiaomichatbot_ph，与 download 一致。"""
        url = f"{BASE_URL}/open-apis/host-files/list"
        headers = {
            "Content-Type": "application/json",
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/",
        }
        r = requests.get(
            url,
            cookies=COOKIES,
            headers=headers,
            params={"path": path, "xiaomichatbot_ph": PH},
            timeout=15,
        )
        d = r.json()
        if d.get("code") == 0:
            return d["data"].get("items", [])
        return None

    def http_chat_conversation_list(self, page_num=1, page_size=20):
        """POST /open-apis/chat/conversation/list?xiaomichatbot_ph=...（与 aistudio 抓包一致，按账号会话列表）。"""
        url = f"{BASE_URL}/open-apis/chat/conversation/list"
        headers = {
            "Content-Type": "application/json",
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/",
        }
        r = requests.post(
            url,
            cookies=COOKIES,
            headers=headers,
            params={"xiaomichatbot_ph": PH},
            json={"pageInfo": {"pageNum": page_num, "pageSize": page_size}},
            timeout=15,
        )
        d = r.json()
        if d.get("code") == 0:
            return d.get("data")
        return None
    
    def http_download_file(self, path, save_to=None):
        """通过 HTTP API 下载文件（获取 FDS 链接并下载）"""
        url = f"{BASE_URL}/open-apis/host-files/download?xiaomichatbot_ph={quote(PH)}"
        headers = {"Content-Type": "application/json", "Origin": BASE_URL, "Referer": f"{BASE_URL}/"}
        r = requests.post(url, cookies=COOKIES, headers=headers, json={"path": path}, timeout=15)
        d = r.json()
        if d.get("code") != 0:
            return None
        resource_url = d["data"].get("resourceUrl")
        if not resource_url:
            return None
        resp = requests.get(resource_url, timeout=30)
        if resp.status_code == 200:
            save_path = save_to or path.split("/")[-1]
            with open(save_path, "w") as f:
                f.write(resp.text)
            print(f"已保存到: {save_path}", file=sys.stderr)
            return resp.text
        return None
    
    def close(self):
        if self.ws:
            self.ws.close()


def chat_interactive(client):
    print("交互式聊天 (quit=退出, /history=历史, /files=文件, /read <name>=读取, /download <name> [save]=下载)")
    print("-" * 60)
    while True:
        try:
            msg = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not msg: continue
        if msg.lower() in ("quit", "exit", "q"): break
        if msg == "/history":
            for m in client.get_history(10):
                role = "你" if m["role"] == "user" else "Claw"
                for c in m.get("content", []):
                    if c.get("type") == "text": print(f"  [{role}] {c['text'][:100]}")
            continue
        if msg == "/files":
            files = client.list_files()
            for f in files:
                print(f"  📄 {f['name']}  ({f.get('size', 0)} bytes)")
            continue
        if msg.startswith("/read "):
            name = msg[6:].strip()
            content = client.read_file(name)
            if content is not None:
                print(f"\n--- {name} ---\n{content[:2000]}")
            else:
                print(f"  读取失败: {name}")
            continue
        if msg.startswith("/download "):
            parts = msg.split(" ", 2)
            name = parts[1]
            save_to = parts[2] if len(parts) > 2 else name
            if client.download_file(name, save_to):
                print(f"  ✅ 已下载")
            else:
                print(f"  ❌ 下载失败")
            continue
        
        print("Claw: ", end="", flush=True)
        print(client.send_message(msg))


def main():
    if len(sys.argv) < 2:
        print("用法: python3 claw_chat.py <命令> [参数]")
        print()
        print("命令:")
        print("  chat                              交互式聊天")
        print("  send <消息>                       发送单条消息")
        print("  history                           聊天历史")
        print("  sessions                          会话列表")
        print("  conversations                     聊天会话列表 (HTTP POST conversation/list)")
        print("  files                             工作区文件列表 (HTTP API)")
        print("  ls [path]                        列出目录 (HTTP API，含env等隐藏文件)")
        print("  read <文件名>                     读取文件 (如 IDENTITY.md)")
        print("  download <路径> [保存路径]        下载文件 (HTTP API，支持任意路径)")
        print("  create                            创建/续期 Claw 实例")
        print("  users                             列出所有用户")
        print("  add-user <name> <uid> <token>     添加用户")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    # 用户管理命令不需要连接
    if cmd == "users":
        list_users()
        return
    if cmd == "add-user":
        if len(sys.argv) < 5:
            print("用法: claw_chat.py add-user <name> <userId> <serviceToken> [ph]", file=sys.stderr)
            sys.exit(1)
        name = sys.argv[2]
        uid = sys.argv[3]
        token = sys.argv[4]
        ph = sys.argv[5] if len(sys.argv) > 5 else "kHbEyClURiAkISDYkZ2reQ=="
        add_user(name, uid, token, ph)
        return
    
    # 加载用户
    user_id = os.environ.get("CLAW_USER")
    load_user(user_id)
    
    print("正在连接 Claw...", file=sys.stderr)
    client = ClawClient()
    if not client.connect():
        print("连接失败!", file=sys.stderr)
        sys.exit(1)
    print("已连接!", file=sys.stderr)
    
    try:
        if cmd == "chat":
            chat_interactive(client)
        elif cmd == "send":
            if len(sys.argv) < 3: sys.exit(1)
            print(client.send_message(" ".join(sys.argv[2:])))
        elif cmd == "history":
            for m in client.get_history(20):
                role = "你" if m["role"] == "user" else "Claw"
                for c in m.get("content", []):
                    if c.get("type") == "text": print(f"[{role}] {c['text'][:150]}")
        elif cmd == "sessions":
            for s in client.list_sessions():
                print(f"  {s['key']}  ({s.get('kind', '?')})")
        elif cmd == "conversations":
            data = client.http_chat_conversation_list()
            if data is not None:
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                print("会话列表获取失败", file=sys.stderr)
        elif cmd == "files":
            for f in client.list_files():
                print(f"📄 {f['name']}  ({f.get('size', 0)} bytes)")
        elif cmd == "ls":
            path = sys.argv[2] if len(sys.argv) > 2 else "/root/.openclaw/workspace"
            items = client.http_list_files(path)
            if items is not None:
                print(f"\U0001f4c1 {path} ({len(items)} 项):")
                for item in items:
                    kind = "\U0001f4c1" if item.get("directory") else "\U0001f4c4"
                    print(f"  {kind} {item['name']}  ({item.get('size', 0)} bytes)")
            else:
                print(f"(API 不支持 {path}，尝试 Claw...)", file=sys.stderr)
                reply = client.send_message(f"执行 ls -la {path}，以表格形式返回：权限 用户 大小 日期 文件名。只返回数据。")
                print(reply)

        elif cmd == "read":
            if len(sys.argv) < 3: sys.exit(1)
            content = client.read_file(sys.argv[2])
            print(content if content is not None else f"读取失败: {sys.argv[2]}")
        elif cmd == "create":
            import requests as req
            from urllib.parse import quote as q
            _post_agreement_mimo_claw()
            r = req.post(
                f"{BASE_URL}/open-apis/user/mimo-claw/create?xiaomichatbot_ph={q(PH)}",
                cookies=COOKIES,
                headers=_aistudio_cors_json_headers(),
                timeout=30,
            )
            d = r.json()
            if d["code"] == 0:
                s = d["data"]
                import datetime
                exp = datetime.datetime.fromtimestamp(s["expireTime"]/1000).strftime("%Y-%m-%d %H:%M")
                print(f"状态: {s['status']} — {s['message']}")
                print(f"过期: {exp}")
            else:
                print(f"失败: {d['msg']}")

        elif cmd == "download":
            if len(sys.argv) < 3: sys.exit(1)
            name = sys.argv[2]
            save_to = sys.argv[3] if len(sys.argv) > 3 else name.split("/")[-1]
            # Auto-prepend workspace path if not absolute
            dl_path = name if name.startswith("/") else f"/root/.openclaw/workspace/{name}"
            # Try HTTP API first (can get any file including env files)
            result = client.http_download_file(dl_path, save_to)
            if not result:
                # Fallback to WS API
                if not client.download_file(name, save_to):
                    print(f"下载失败: {name}", file=sys.stderr)
    finally:
        client.close()


if __name__ == "__main__":
    main()
