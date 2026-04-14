#!/usr/bin/env python3
"""
高性能 Web 面板：FastAPI + uvicorn，异步全链路。
启动: python claw_web_fast.py
"""

import asyncio
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from web_core import (
    state,
    log_tag,
    WEB_PANEL_PORT,
    KEY_VALID_DURATION,
    load_users,
    save_users,
    load_app_state,
    save_app_state,
    sync_mimo_key_from_app_state,
    persist_mimo_key_to_app_state,
    persist_oc_to_user_panel,
    build_relay_oc_pool,
    pick_relay_oc_round_robin,
    iter_relay_oc_display_rows,
    probe_account_aistudio,
    fetch_mimo_claw_experience,
    probe_mimo_oc_via_api,
    probe_mimo_oc_via_api_key,
    force_refresh_mimo_key_via_claw,
    parse_credentials_auto,
    _apply_claw_credentials_sync,
    retry_on_401,
    verify_relay_client_authorization,
    get_next_validation_display,
    get_http_client,
    norm_uid,
    resolve_user_key,
    validate_key,
    oc_key_preview,
    _check_oc_expired,
    _append_oc_history_sync,
    MIMO_BASE_URL,
    apply_model_mapping,
    build_mimo_json_headers,
    transform_mimo_response_json,
    chat_completion_log_summary,
)
from mimo_openai_shared import MIMO_BASE_URL as _MBU
from mimi2_responses import create_responses_router


# ──────────────────── 生命周期 ────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    await sync_mimo_key_from_app_state()
    monitor_task = asyncio.create_task(key_monitor())
    state.log("Web panel started (FastAPI)")
    yield
    monitor_task.cancel()
    client = get_http_client()
    if client and not client.is_closed:
        await client.aclose()


app = FastAPI(title="MiMo Control Panel", lifespan=lifespan)

# ──────────────────── Responses API 兼容层 ────────────────────
responses_router = create_responses_router(
    get_http_client=get_http_client,
    build_mimo_json_headers=build_mimo_json_headers,
    apply_model_mapping=apply_model_mapping,
    MIMO_BASE_URL=MIMO_BASE_URL,
    verify_relay_client_authorization=verify_relay_client_authorization,
    build_relay_oc_pool=build_relay_oc_pool,
    pick_relay_oc_round_robin=pick_relay_oc_round_robin,
    retry_on_401=retry_on_401,
)
app.include_router(responses_router)

# 静态文件和模板目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount(
    "/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static"
)


def _load_template() -> str:
    path = os.path.join(BASE_DIR, "templates", "index.html")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


HTML_TEMPLATE = _load_template()


# ──────────────────── 面板路由 ────────────────────


@app.get("/", response_class=HTMLResponse)
async def index():
    logs_html = "<br>".join(state.logs[-50:])
    return HTMLResponse(HTML_TEMPLATE.replace("{{ logs }}", logs_html))


# ──────────────────── API 路由 ────────────────────


@app.get("/api/status")
async def api_status():
    users_data = await load_users()
    app_state = await load_app_state()
    du = resolve_user_key(users_data.get("users", {}), users_data.get("default", "1"))
    state.active_user = du or users_data.get("default", "1")
    return {
        "status": state.task_status,
        "mimo_key": state.mimo_api_key,
        "last_refresh": time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(state.last_key_refresh)
        )
        if state.last_key_refresh
        else "Never",
        "next_check": await get_next_validation_display(),
        "users": users_data.get("users", {}),
        "default_user": users_data.get("default", "1"),
        "active_user": state.active_user,
        "last_refresh_error": state.last_refresh_error,
        "oc_max_retry": app_state.get("oc_max_retry", 3),
    }


@app.get("/api/logs")
async def api_logs():
    return Response("<br>".join(state.logs[-100:]), media_type="text/html")


@app.post("/api/ui_log")
async def api_ui_log(req: Request):
    data = await req.json()
    msg = (data.get("message") or "").strip()
    if msg:
        state.log(f"[Panel] {msg}")
    return {"success": True}


@app.post("/api/clear_logs")
async def api_clear_logs():
    state.logs.clear()
    state.log("[Panel] Logs cleared")
    return {"success": True}


@app.get("/api/accounts_health")
async def api_accounts_health():
    users_data = await load_users()
    du = resolve_user_key(users_data.get("users", {}), users_data.get("default", "1"))
    state.active_user = du or users_data.get("default", "1")
    accounts = []
    for uid, user in users_data.get("users", {}).items():
        r = await probe_account_aistudio(user)
        accounts.append(
            {
                "uid": str(uid),
                "name": user.get("name") or "Unnamed",
                "userId": user.get("userId") or "",
                "ok": r["ok"],
                "http_status": r.get("http_status"),
                "message": r.get("message") or "",
            }
        )
    return {
        "accounts": accounts,
        "count": len(accounts),
        "default_user": str(users_data.get("default", "")),
    }


@app.post("/api/account_trial")
async def api_account_trial(req: Request):
    data = await req.json()
    raw_uid = data.get("user_id")
    users_data = await load_users()
    uid = resolve_user_key(users_data.get("users", {}), raw_uid)
    if not uid:
        return JSONResponse({"success": False, "error": "User not found"}, 400)
    user = users_data["users"][uid]
    ex = await fetch_mimo_claw_experience(user)
    # 缓存
    try:
        ex_c = dict(ex)
        ex_c["_cache_ts"] = time.time()
        user["experience_cache"] = ex_c
    except Exception:
        pass
    if ex.get("ok") and not ex.get("no_account"):
        if user.get("mimo_trial_no_expire"):
            user.pop("mimo_trial_no_expire", None)
            await save_users(users_data)
    out = {"success": True, **ex}
    # 默认账号写入 expire
    def_uid = resolve_user_key(users_data.get("users", {}), users_data.get("default"))
    exp_ms = ex.get("expire_ms")
    if def_uid and uid == def_uid and exp_ms is not None:
        try:
            exp_ms = int(exp_ms)
            if exp_ms > 0:
                st = await load_app_state()
                st["experience_expire_ms"] = exp_ms
                await save_app_state(st)
        except (TypeError, ValueError):
            pass
    return out


@app.post("/api/account_copy_line")
async def api_account_copy_line(req: Request):
    data = await req.json()
    raw_uid = data.get("user_id")
    users_data = await load_users()
    if raw_uid not in (None, ""):
        rk = resolve_user_key(users_data.get("users", {}), raw_uid)
        if not rk:
            return JSONResponse({"success": False, "error": "User not found"}, 400)
        u = users_data["users"][rk]
        key = (u.get("mimo_api_key") or "").strip()
        if key and validate_key(key):
            return {"success": True, "line": key}
        return JSONResponse({"success": False, "error": "No OC, fetch first"}, 400)
    for rk, u in users_data.get("users", {}).items():
        key = (u.get("mimo_api_key") or "").strip()
        if key and validate_key(key):
            return {"success": True, "line": key}
    return JSONResponse({"success": False, "error": "No OC available"}, 400)


@app.post("/api/manual_refresh")
async def api_manual_refresh():
    await sync_mimo_key_from_app_state()
    if not state.mimo_api_key:
        return {
            "success": False,
            "error": "No OC, import credentials or fetch via Claw",
        }
    if not validate_key(state.mimo_api_key):
        if not await force_refresh_mimo_key_via_claw():
            return {
                "success": False,
                "error": state.last_refresh_error or "Refresh failed",
            }
        return {"success": True, "message": "Refreshed (invalid format)"}
    p = await probe_mimo_oc_via_api()
    if p is True:
        return {"success": True, "message": "OC valid (chat probe passed)"}
    if p is False:
        state.log("Manual refresh: OC 401, refreshing via Claw...")
        if await force_refresh_mimo_key_via_claw():
            return {"success": True, "message": "OC refreshed via Claw"}
        return {"success": False, "error": state.last_refresh_error or "Refresh failed"}
    return {"success": False, "error": "Probe inconclusive"}


@app.post("/api/claw_refetch_oc")
async def api_claw_refetch_oc(req: Request):
    data = (
        await req.json()
        if req.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    raw_uid = data.get("user_id")
    users_data = await load_users()
    uid = None
    if raw_uid not in (None, ""):
        uid = resolve_user_key(users_data.get("users", {}), raw_uid)
        if not uid:
            return JSONResponse({"success": False, "error": "User not found"}, 400)
    if not await force_refresh_mimo_key_via_claw(uid_pref=uid):
        return {"success": False, "error": state.last_refresh_error or "Failed"}
    users_data = await load_users()
    clear_u = uid or state.active_user
    if clear_u and clear_u in users_data.get("users", {}):
        users_data["users"][clear_u].pop("mimo_trial_no_expire", None)
        await save_users(users_data)
    return {"success": True, "message": "OC refreshed via Claw"}


@app.post("/api/import_credentials")
async def api_import_credentials(req: Request):
    data = await req.json()
    text = data.get("credentials", "")
    if not text.strip():
        return {"success": False, "error": "Empty"}
    credentials = parse_credentials_auto(text)
    if not credentials:
        return {"success": False, "error": "Parse failed"}
    users_data = await load_users()
    count = 0
    for cred in credentials:
        if not all(
            [cred.get("userId"), cred.get("serviceToken"), cred.get("xiaomichatbot_ph")]
        ):
            continue
        existing = None
        for uid, u in users_data.get("users", {}).items():
            if u.get("userId") == cred["userId"]:
                existing = uid
                break
        if existing:
            users_data["users"][existing].update(cred)
        else:
            ids = set(users_data.get("users", {}).keys())
            uid = str(max([int(k) for k in ids] + [0]) + 1)
            users_data["users"][uid] = cred
        count += 1
    if count > 0:
        await save_users(users_data)
        return {"success": True, "message": f"Imported {count} users"}
    return {"success": False, "error": "No valid credentials"}


@app.post("/api/set_default_user")
async def api_set_default_user(req: Request):
    data = await req.json()
    users_data = await load_users()
    rk = resolve_user_key(users_data.get("users", {}), data.get("user_id"))
    if rk:
        users_data["default"] = rk
        state.active_user = rk
        await save_users(users_data)
        return {"success": True, "default_user": rk}
    return {"success": False, "error": "User not found"}


@app.post("/api/delete_user")
async def api_delete_user(req: Request):
    data = await req.json()
    users_data = await load_users()
    uid = resolve_user_key(users_data.get("users", {}), data.get("user_id"))
    if not uid:
        return {"success": False, "error": "User not found"}
    row = users_data["users"].pop(uid, None)
    mi_uid = row.get("userId") if row else None
    remaining = list(users_data.get("users", {}).keys())
    users_data["default"] = remaining[0] if remaining else "1"
    if mi_uid is not None:
        fp = os.path.join("users", f"user_{mi_uid}.json")
        try:
            if os.path.isfile(fp):
                os.remove(fp)
        except OSError as e:
            state.log(f"Delete file failed: {e}")
    await save_users(users_data)
    return {"success": True}


@app.post("/api/update_app_state")
async def api_update_app_state(req: Request):
    data = await req.json()
    st = await load_app_state()
    for k, v in data.items():
        st[k] = v
    await save_app_state(st)
    return {"success": True}


@app.post("/api/destroy_claw")
async def api_destroy_claw():
    from claw_chat import COOKIES as _C, PH as _P, BASE_URL as _B

    users_data = await load_users()
    if not state.active_user:
        state.active_user = users_data.get("default", "1")
    ok, rk_or_err = _apply_claw_credentials_sync(users_data, state.active_user)
    if not ok:
        return {"success": False, "error": rk_or_err}
    state.active_user = rk_or_err
    ph = _C.get("xiaomichatbot_ph", "") or _P
    destroy_url = f"{_B}/open-apis/user/mimo-claw/destroy?xiaomichatbot_ph={quote(str(ph), safe='')}"
    cookies = {
        "serviceToken": _C.get("serviceToken", ""),
        "xiaomichatbot_ph": _C.get("xiaomichatbot_ph", ""),
        "userId": str(_C.get("userId", "")),
    }
    try:
        client = get_http_client()
        r = await client.post(
            destroy_url,
            cookies=cookies,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        result = r.json()
        if result.get("code") == 0:
            await asyncio.sleep(3)
            status_url = f"{_B}/open-apis/user/mimo-claw/status"
            sr = await client.get(status_url, cookies=cookies, timeout=30)
            sresult = sr.json()
            if sresult.get("code") == 0:
                s = sresult.get("data", {}).get("status")
                if s == "DESTROYED":
                    if state.mimo_api_key and validate_key(state.mimo_api_key):
                        _append_oc_history_sync(state.mimo_api_key, "destroyed")
                    state.mimo_api_key = None
                    state.last_key_refresh = 0
                    st = await load_app_state()
                    st["current_api_key"] = ""
                    st["experience_expire_ms"] = None
                    st["last_key_refresh_ts"] = None
                    await save_app_state(st)
                    return {"success": True, "message": "Claw destroyed"}
                return {"success": True, "message": f"Status: {s}"}
            return {
                "success": False,
                "error": f"Status check failed: {sresult.get('msg')}",
            }
        return {"success": False, "error": f"Destroy failed: {result.get('msg')}"}
    except Exception as e:
        state.log(f"Destroy error: {e}")
        return {"success": False, "error": str(e)}


@app.get("/api/oc_catalog")
async def api_oc_catalog():
    users_data = await load_users()
    def_uid = str(users_data.get("default") or "")
    rows = [r async for r in iter_relay_oc_display_rows()]
    relay_entries = []
    for row in rows:
        u = row["user"]
        oc_key = row["oc_key"]
        saved_at_raw = (row.get("saved_at") or "").strip()
        saved_at = saved_at_raw or "—"
        oc_expired = _check_oc_expired(saved_at_raw)
        panel_uid = row["uid"]
        title = f"{u.get('name') or 'Unnamed'} · {u.get('userId') or ''}"
        if row.get("uses_global_fallback"):
            title += " (global)"
        is_def = str(panel_uid) == def_uid
        # 体验数据优先用缓存
        cached_exp = u.get("experience_cache")
        now_ts = time.time()
        if (
            cached_exp
            and isinstance(cached_exp, dict)
            and cached_exp.get("_cache_ts")
            and now_ts - cached_exp.get("_cache_ts", 0) < 300
        ):
            trial = cached_exp
        else:
            trial = await fetch_mimo_claw_experience(u)
            try:
                tc = dict(trial)
                tc["_cache_ts"] = now_ts
                u["experience_cache"] = tc
            except Exception:
                pass
        relay_entries.append(
            {
                "uid": str(panel_uid),
                "title": title,
                "is_default": is_def,
                "preview": oc_key_preview(oc_key),
                "saved_at": saved_at,
                "oc_expired": oc_expired,
                "trial": trial,
                "excluded_from_relay": bool(u.get("mimo_trial_no_expire")),
            }
        )
    pool = await build_relay_oc_pool()
    import asyncio as _a

    loop = _a.get_event_loop()
    hist = await loop.run_in_executor(
        None, lambda: __import__("web_core")._load_oc_history_sync()
    )
    return {
        "relay_entries": relay_entries,
        "relay_pool_size": len(relay_entries),
        "relay_unique_keys": len(pool),
        "default_user": def_uid,
        "history": hist.get("entries", [])[:50],
    }


# ──────────────────── OpenAI 中转路由 ────────────────────


@app.get("/v1")
@app.get("/v1/")
async def openai_v1_index():
    return {
        "object": "relay_info",
        "service": "mimo2api",
        "endpoints": {
            "chat_completions": "POST /v1/chat/completions",
            "models": "GET /v1/models",
        },
    }


@app.post("/v1/chat/completions")
async def openai_chat_completions(request: Request):
    auth = request.headers.get("Authorization", "")
    if not verify_relay_client_authorization(auth):
        return JSONResponse(
            {"error": {"message": "Invalid API key", "type": "authentication_error"}},
            401,
        )

    if not await build_relay_oc_pool():
        users_data = await load_users()
        users = users_data.get("users", {})
        for rk, u in users.items():
            k = (u.get("mimo_api_key") or "").strip()
            if not k or not validate_key(k):
                if await force_refresh_mimo_key_via_claw(uid_pref=rk):
                    break
        if not await build_relay_oc_pool():
            return JSONResponse(
                {
                    "error": {
                        "message": "MIMO API key unavailable",
                        "type": "authentication_error",
                    }
                },
                401,
            )

    async def send(oc_key, rk):
        raw = await request.json()
        data = dict(raw) if isinstance(raw, dict) else {}
        # 清理空 content 消息
        if "messages" in data and isinstance(data["messages"], list):
            cleaned = []
            for msg in data["messages"]:
                if isinstance(msg, dict) and isinstance(msg.get("content"), list) and len(msg["content"]) == 0:
                    continue
                cleaned.append(msg)
            data["messages"] = cleaned if cleaned else data["messages"]
        apply_model_mapping(data)
        headers = build_mimo_json_headers(oc_key)
        client = get_http_client()
        if data.get("stream"):
            r = await client.post(
                f"{MIMO_BASE_URL}/v1/chat/completions",
                json=data,
                headers=headers,
                timeout=120,
            )
            if r.status_code == 401:
                return Response(
                    r.content,
                    status_code=401,
                    media_type=r.headers.get("content-type", "application/json"),
                )
            ct = r.headers.get("content-type", "text/event-stream; charset=utf-8")

            async def gen():
                async for chunk in r.aiter_bytes(chunk_size=8192):
                    if chunk:
                        yield chunk

            return StreamingResponse(gen(), status_code=200, media_type=ct)
        else:
            r = await client.post(
                f"{MIMO_BASE_URL}/v1/chat/completions",
                json=data,
                headers=headers,
                timeout=120,
            )
            if r.status_code == 401:
                return Response(
                    r.content,
                    status_code=401,
                    media_type=r.headers.get("content-type", "application/json"),
                )
            try:
                td = r.json()
            except Exception:
                td = r.text
            return JSONResponse(td, status_code=r.status_code)

    return await retry_on_401(send)


@app.get("/v1/models")
async def openai_list_models(request: Request):
    auth = request.headers.get("Authorization", "")
    if not verify_relay_client_authorization(auth):
        return JSONResponse(
            {"error": {"message": "Invalid API key", "type": "authentication_error"}},
            401,
        )

    if not await build_relay_oc_pool():
        return JSONResponse(
            {
                "error": {
                    "message": "MIMO API key unavailable",
                    "type": "authentication_error",
                }
            },
            401,
        )

    async def send(oc_key, rk):
        headers = build_mimo_json_headers(oc_key)
        r = await get_http_client().get(
            f"{MIMO_BASE_URL}/v1/models", headers=headers, timeout=30
        )
        if r.status_code == 401:
            return Response(
                r.content,
                status_code=401,
                media_type=r.headers.get("content-type", "application/json"),
            )
        try:
            td = r.json()
        except Exception:
            td = r.text
        return JSONResponse(td, status_code=r.status_code)

    return await retry_on_401(send)


@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def openai_proxy_all(request: Request, path: str):
    auth = request.headers.get("Authorization", "")
    if not verify_relay_client_authorization(auth):
        return JSONResponse(
            {"error": {"message": "Invalid API key", "type": "authentication_error"}},
            401,
        )
    if not await build_relay_oc_pool():
        return JSONResponse(
            {
                "error": {
                    "message": "MIMO API key unavailable",
                    "type": "authentication_error",
                }
            },
            401,
        )

    async def send(oc_key, rk):
        headers = dict(request.headers)
        headers["Authorization"] = f"Bearer {oc_key}"
        headers.pop("Host", None)
        url = f"{MIMO_BASE_URL}/v1/{path}"
        client = get_http_client()
        if request.method in ["POST", "PUT", "PATCH"]:
            body = await request.body()
            r = await client.request(
                request.method,
                url,
                content=body,
                headers=headers,
                params=dict(request.query_params),
                timeout=120,
            )
        else:
            r = await client.request(
                request.method,
                url,
                headers=headers,
                params=dict(request.query_params),
                timeout=120,
            )
        if r.status_code == 401:
            return Response(
                r.content,
                status_code=401,
                media_type=r.headers.get("content-type", "application/json"),
            )
        try:
            td = r.json()
        except Exception:
            td = r.text
        return JSONResponse(
            td if isinstance(td, dict) else {"text": td}, status_code=r.status_code
        )

    return await retry_on_401(send)


# ──────────────────── 后台密钥监控 ────────────────────


async def key_monitor():
    """后台监控：每5分钟检查OC，过期立即刷新"""
    while True:
        await asyncio.sleep(5 * 60)  # 每5分钟检查一次
        try:
            if not state.mimo_api_key:
                await sync_mimo_key_from_app_state()

            # 检查所有OC，找出过期的
            pool = await build_relay_oc_pool()
            need_refresh = False

            if not pool:
                state.log("Monitor: 没有可用OC，主动获取...")
                await force_refresh_mimo_key_via_claw()
                continue

            for rk, key in pool:
                token = log_tag.set(f"账号 {rk}")
                try:
                    # 探测OC是否有效
                    probe = await probe_mimo_oc_via_api_key(key)
                    if probe is False:
                        state.log(f"Monitor: 账号 {rk} OC已过期，重新申请...")
                        await force_refresh_mimo_key_via_claw(uid_pref=rk)
                        need_refresh = True
                    elif probe is None:
                        # 探测失败，也重新申请
                        state.log(f"Monitor: 账号 {rk} OC探测失败，重新申请...")
                        await force_refresh_mimo_key_via_claw(uid_pref=rk)
                        need_refresh = True
                finally:
                    log_tag.reset(token)

            if not need_refresh:
                # 检查OC创建时间（内存中）
                if state.oc_created_at:
                    age_minutes = (time.time() - state.oc_created_at) / 60
                    if age_minutes > 50:
                        state.log(
                            f"Monitor: OC已创建 {age_minutes:.0f} 分钟，主动刷新..."
                        )
                        await force_refresh_mimo_key_via_claw()

        except asyncio.CancelledError:
            break
        except Exception as e:
            state.log(f"Monitor error: {e}")


# ──────────────────── 启动 ────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=WEB_PANEL_PORT, log_level="info")
