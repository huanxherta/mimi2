#!/usr/bin/env python3
"""
OpenAI Responses API 兼容层：将 /v1/responses 请求转换为 /v1/chat/completions。
支持流式和非流式响应。
"""

import json
import time
import uuid
import asyncio
from typing import Any, Dict, List, Optional, Union

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse


# ──────────────────── 转换函数 ────────────────────

def responses_to_chat_completion(req_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 OpenAI Responses API 请求转换为 Chat Completions 格式。

    Responses API 格式:
    {
        "model": "gpt-4o",
        "input": "Hello" | [{"role": "user", "content": "Hello"}],
        "instructions": "You are helpful",
        "temperature": 0.7,
        "max_output_tokens": 1024,
        "stream": false,
        ...
    }

    Chat Completions 格式:
    {
        "model": "gpt-4o",
        "messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}],
        "temperature": 0.7,
        "max_tokens": 1024,
        "stream": false,
        ...
    }
    """
    messages = []

    # instructions → system message
    instructions = req_data.get("instructions")
    if instructions:
        if isinstance(instructions, str):
            messages.append({"role": "system", "content": instructions})
        elif isinstance(instructions, list):
            # instructions 可以是 input item 列表
            for item in instructions:
                if isinstance(item, dict) and item.get("role") == "system":
                    messages.append({"role": "system", "content": item.get("content", "")})

    # input → messages
    inp = req_data.get("input", "")
    if isinstance(inp, str):
        # 纯文本 input → user message
        messages.append({"role": "user", "content": inp})
    elif isinstance(inp, list):
        for item in inp:
            if not isinstance(item, dict):
                continue
            role = item.get("role", "user")
            item_type = item.get("type", "message")

            if item_type == "message":
                content = item.get("content", "")
                if isinstance(content, str):
                    messages.append({"role": role, "content": content})
                elif isinstance(content, list):
                    # 多模态内容
                    chat_content = []
                    for part in content:
                        if not isinstance(part, dict):
                            continue
                        ptype = part.get("type", "text")
                        if ptype == "input_text":
                            chat_content.append({"type": "text", "text": part.get("text", "")})
                        elif ptype == "input_image":
                            url = part.get("image_url", "")
                            detail = part.get("detail", "auto")
                            chat_content.append({
                                "type": "image_url",
                                "image_url": {"url": url, "detail": detail}
                            })
                        elif ptype == "text":
                            chat_content.append({"type": "text", "text": part.get("text", "")})
                    if chat_content:
                        messages.append({"role": role, "content": chat_content})
            elif item_type == "function_call":
                # function call output → tool message
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": item.get("call_id", f"call_{uuid.uuid4().hex[:24]}"),
                        "type": "function",
                        "function": {
                            "name": item.get("name", ""),
                            "arguments": item.get("arguments", "{}")
                        }
                    }]
                })
            elif item_type == "function_call_output":
                messages.append({
                    "role": "tool",
                    "tool_call_id": item.get("call_id", ""),
                    "content": item.get("output", "")
                })

    # 构建 chat completions 请求
    chat_req: Dict[str, Any] = {
        "model": req_data.get("model", "gpt-4o"),
        "messages": messages,
    }

    # 直接透传的参数
    passthrough_keys = [
        "temperature", "top_p", "n", "stop", "presence_penalty",
        "frequency_penalty", "logit_bias", "user", "seed",
        "logprobs", "top_logprobs", "response_format", "tools",
        "tool_choice", "parallel_tool_calls",
    ]
    for key in passthrough_keys:
        if key in req_data and req_data[key] is not None:
            chat_req[key] = req_data[key]

    # max_output_tokens → max_tokens
    max_output = req_data.get("max_output_tokens")
    if max_output is not None:
        chat_req["max_tokens"] = max_output

    # stream
    if req_data.get("stream"):
        chat_req["stream"] = True
        chat_req["stream_options"] = {"include_usage": True}

    return chat_req


def chat_completion_to_responses(
    chat_resp: Dict[str, Any],
    model: str = "gpt-4o",
    req_id: str = "",
) -> Dict[str, Any]:
    """
    将 Chat Completions 响应转换为 Responses API 格式。

    Chat Completions 响应:
    {
        "id": "chatcmpl-xxx",
        "object": "chat.completion",
        "choices": [{"message": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    }

    Responses API 响应:
    {
        "id": "resp_xxx",
        "object": "response",
        "status": "completed",
        "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Hi"}]}],
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    }
    """
    resp_id = req_id or f"resp_{uuid.uuid4().hex[:24]}"
    now = time.time()

    output = []
    status = "completed"
    finish_reason = None

    choices = chat_resp.get("choices", [])
    if choices:
        choice = choices[0]
        finish_reason = choice.get("finish_reason")
        message = choice.get("message", {})

        # 处理 content
        content = message.get("content", "")
        content_items = []
        if isinstance(content, str) and content:
            content_items.append({
                "type": "output_text",
                "text": content,
                "annotations": []
            })
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        content_items.append({
                            "type": "output_text",
                            "text": part.get("text", ""),
                            "annotations": []
                        })

        # 处理 tool_calls
        tool_calls = message.get("tool_calls", [])
        for tc in tool_calls:
            if isinstance(tc, dict):
                func = tc.get("function", {})
                output.append({
                    "type": "function_call",
                    "id": tc.get("id", f"call_{uuid.uuid4().hex[:24]}"),
                    "call_id": tc.get("id", f"call_{uuid.uuid4().hex[:24]}"),
                    "name": func.get("name", ""),
                    "arguments": func.get("arguments", "{}"),
                    "status": "completed"
                })

        # 添加 message output
        if content_items:
            output.append({
                "type": "message",
                "id": f"msg_{uuid.uuid4().hex[:24]}",
                "status": "completed",
                "role": "assistant",
                "content": content_items
            })

        # 状态映射
        if finish_reason == "length":
            status = "incomplete"
        elif finish_reason == "content_filter":
            status = "incomplete"

    # usage 转换
    usage = None
    chat_usage = chat_resp.get("usage")
    if chat_usage:
        usage = {
            "input_tokens": chat_usage.get("prompt_tokens", 0),
            "output_tokens": chat_usage.get("completion_tokens", 0),
            "total_tokens": chat_usage.get("total_tokens", 0),
        }

    result: Dict[str, Any] = {
        "id": resp_id,
        "object": "response",
        "created_at": now,
        "status": status,
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "max_output_tokens": None,
        "model": chat_resp.get("model", model),
        "output": output,
        "parallel_tool_calls": True,
        "previous_response_id": None,
        "reasoning": None,
        "store": True,
        "temperature": None,
        "text": {"format": {"type": "text"}},
        "tool_choice": "auto",
        "tools": [],
        "top_p": None,
        "truncation": "disabled",
        "usage": usage,
        "user": None,
    }

    if status == "incomplete":
        result["incomplete_details"] = {"reason": finish_reason or "unknown"}

    return result


# ──────────────────── 流式转换 ────────────────────

def chat_chunk_to_responses_event(
    chunk: Dict[str, Any],
    resp_id: str,
    seq: int,
) -> Optional[str]:
    """
    将 Chat Completions 流式 chunk 转换为 Responses API SSE 事件。

    返回 SSE 格式字符串，如: "event: response.output_item.added\\ndata: {...}\\n\\n"
    """
    choices = chunk.get("choices", [])
    usage = chunk.get("usage")

    if not choices and not usage:
        return None

    events = []

    if choices:
        choice = choices[0]
        delta = choice.get("delta", {})
        finish_reason = choice.get("finish_reason")
        index = choice.get("index", 0)

        # content delta
        content = delta.get("content")
        if content is not None:
            if isinstance(content, str):
                event_data = {
                    "type": "response.output_text.delta",
                    "sequence_number": seq,
                    "content_index": 0,
                    "delta": content,
                    "item_id": f"msg_{resp_id}_{index}",
                    "output_index": index,
                    "part": {"type": "text", "text": content, "annotations": []}
                }
                events.append(("response.output_text.delta", event_data))
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        event_data = {
                            "type": "response.output_text.delta",
                            "sequence_number": seq,
                            "content_index": 0,
                            "delta": part.get("text", ""),
                            "item_id": f"msg_{resp_id}_{index}",
                            "output_index": index,
                            "part": {"type": "text", "text": part.get("text", ""), "annotations": []}
                        }
                        events.append(("response.output_text.delta", event_data))

        # tool_calls delta
        tool_calls = delta.get("tool_calls", [])
        for tc in tool_calls:
            if isinstance(tc, dict):
                tc_index = tc.get("index", 0)
                tc_id = tc.get("id", "")
                func = tc.get("function", {})

                if tc_id:
                    # 新的 tool call
                    event_data = {
                        "type": "response.output_item.added",
                        "sequence_number": seq,
                        "item": {
                            "type": "function_call",
                            "id": tc_id,
                            "call_id": tc_id,
                            "name": func.get("name", ""),
                            "arguments": "",
                            "status": "in_progress"
                        },
                        "output_index": tc_index
                    }
                    events.append(("response.output_item.added", event_data))

                args_delta = func.get("arguments", "")
                if args_delta:
                    event_data = {
                        "type": "response.function_call_arguments.delta",
                        "sequence_number": seq,
                        "delta": args_delta,
                        "item_id": tc_id or f"call_{resp_id}_{tc_index}",
                        "output_index": tc_index,
                        "call_id": tc_id or f"call_{resp_id}_{tc_index}"
                    }
                    events.append(("response.function_call_arguments.delta", event_data))

        # finish_reason
        if finish_reason:
            # output_text.done
            event_data = {
                "type": "response.output_text.done",
                "sequence_number": seq,
                "content_index": 0,
                "text": "",
                "item_id": f"msg_{resp_id}_{index}",
                "output_index": index,
            }
            events.append(("response.output_text.done", event_data))

            # output_item.done
            event_data = {
                "type": "response.output_item.done",
                "sequence_number": seq,
                "item": {
                    "type": "message",
                    "id": f"msg_{resp_id}_{index}",
                    "status": "completed",
                    "role": "assistant",
                    "content": []
                },
                "output_index": index
            }
            events.append(("response.output_item.done", event_data))

    # usage (最后一个 chunk)
    if usage:
        event_data = {
            "type": "response.completed",
            "sequence_number": seq,
            "response": {
                "id": resp_id,
                "object": "response",
                "created_at": time.time(),
                "status": "completed",
                "model": chunk.get("model", ""),
                "output": [],
                "usage": {
                    "input_tokens": usage.get("prompt_tokens", 0),
                    "output_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                }
            }
        }
        events.append(("response.completed", event_data))

    # 格式化为 SSE
    sse_parts = []
    for event_type, data in events:
        sse_parts.append(f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n")

    return "".join(sse_parts) if sse_parts else None


# ──────────────────── FastAPI 路由 ────────────────────

def create_responses_router(
    get_http_client,
    build_mimo_json_headers,
    apply_model_mapping,
    MIMO_BASE_URL,
    verify_relay_client_authorization=None,
    build_relay_oc_pool=None,
    pick_relay_oc_round_robin=None,
    retry_on_401=None,
):
    """
    创建 FastAPI 路由，提供 /v1/responses 端点。
    需要传入依赖函数。
    """
    from fastapi import APIRouter

    router = APIRouter()

    @router.post("/v1/responses")
    async def create_response(request: Request):
        """OpenAI Responses API 兼容端点，内部转换为 chat completions。"""
        # 验证客户端 relay key
        auth = request.headers.get("Authorization", "")
        if verify_relay_client_authorization and not verify_relay_client_authorization(auth):
            return JSONResponse(
                {"error": {"message": "Invalid API key", "type": "authentication_error"}},
                status_code=401,
            )

        try:
            req_data = await request.json()
        except Exception:
            return JSONResponse(
                {"error": {"message": "Invalid JSON body", "type": "invalid_request_error"}},
                status_code=400
            )

        resp_id = f"resp_{uuid.uuid4().hex[:24]}"
        model = req_data.get("model", "gpt-4o")
        is_stream = req_data.get("stream", False)

        # 转换为 chat completions 格式
        chat_req = responses_to_chat_completion(req_data)
        apply_model_mapping(chat_req)

        # 获取 OC pool key（和 chat completions 一样）
        if build_relay_oc_pool and pick_relay_oc_round_robin:
            if not await build_relay_oc_pool():
                return JSONResponse(
                    {"error": {"message": "MIMO API key unavailable", "type": "authentication_error"}},
                    status_code=401,
                )
            _picked = await pick_relay_oc_round_robin()
            oc_key = _picked[1] if _picked else None
            if not oc_key:
                return JSONResponse(
                    {"error": {"message": "No available MIMO key", "type": "authentication_error"}},
                    status_code=401,
                )
        else:
            # 兼容旧调用方式
            api_key = auth.replace("Bearer ", "") if auth.startswith("Bearer ") else ""
            oc_key = api_key

        headers = build_mimo_json_headers(oc_key)
        url = f"{MIMO_BASE_URL}/v1/chat/completions"

        client = get_http_client()

        async def _send_request(oc_key_inner=None, rk=None):
            """用指定 key 发送请求，支持 401 重试。"""
            use_key = oc_key_inner if oc_key_inner else oc_key
            h = build_mimo_json_headers(use_key)
            if is_stream:
                return await _do_stream(client, url, chat_req, h, resp_id, model)
            else:
                return await _do_nonstream(client, url, chat_req, h, resp_id, model)

        if retry_on_401:
            return await retry_on_401(_send_request)
        else:
            return await _send_request()

    async def _do_stream(client, url, chat_req, headers, resp_id, model):
        """流式响应。"""
        # 预检：非流式请求检查 401（避免缓冲整个流式响应）
        preflight = {
            "model": chat_req.get("model"),
            "messages": [{"role": "user", "content": "."}],
            "max_tokens": 1,
            "stream": False,
        }
        try:
            pf = await client.post(url, json=preflight, headers=headers, timeout=30)
            if pf.status_code == 401:
                return JSONResponse(
                    {"error": {"message": "MIMO 401", "type": "authentication_error"}},
                    status_code=401,
                )
        except Exception:
            pass

        async def stream_generator():
            seq = 0
            # 发送 response.created 事件
            created_event = {
                "type": "response.created",
                "sequence_number": seq,
                "response": {
                    "id": resp_id,
                    "object": "response",
                    "created_at": time.time(),
                    "status": "in_progress",
                    "model": model,
                    "output": [],
                    "usage": None
                }
            }
            yield f"event: response.created\ndata: {json.dumps(created_event, ensure_ascii=False)}\n\n"
            seq += 1

            # 发送 response.in_progress 事件
            in_progress_event = {
                "type": "response.in_progress",
                "sequence_number": seq,
                "response": {
                    "id": resp_id,
                    "object": "response",
                    "created_at": time.time(),
                    "status": "in_progress",
                    "model": model,
                    "output": [],
                    "usage": None
                }
            }
            yield f"event: response.in_progress\ndata: {json.dumps(in_progress_event, ensure_ascii=False)}\n\n"
            seq += 1

            try:
                async with client.stream("POST", url, json=chat_req, headers=headers, timeout=120) as resp:
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        sse = chat_chunk_to_responses_event(chunk, resp_id, seq)
                        if sse:
                            yield sse
                            seq += 1

            except Exception as e:
                error_event = {
                    "type": "error",
                    "sequence_number": seq,
                    "code": "server_error",
                    "message": str(e),
                    "param": None
                }
                yield f"event: error\ndata: {json.dumps(error_event, ensure_ascii=False)}\n\n"

            # 发送 [DONE]
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )

    async def _do_nonstream(client, url, chat_req, headers, resp_id, model):
        """非流式响应。"""
        try:
            resp = await client.post(url, json=chat_req, headers=headers, timeout=120)
            chat_resp = resp.json()

            if resp.status_code != 200:
                return JSONResponse(chat_resp, status_code=resp.status_code)

            responses_resp = chat_completion_to_responses(chat_resp, model, resp_id)
            return JSONResponse(responses_resp)

        except httpx.TimeoutException:
            return JSONResponse(
                {"error": {"message": "Upstream timeout", "type": "server_error"}},
                status_code=504
            )
        except Exception as e:
            return JSONResponse(
                {"error": {"message": str(e), "type": "server_error"}},
                status_code=500
            )

    return router
