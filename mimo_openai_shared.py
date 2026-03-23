#!/usr/bin/env python3
"""
MIMO OpenAI 兼容中转：共享常量与纯函数（不依赖 Flask 应用状态）。
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MIMO_BASE_URL = "https://api.xiaomimimo.com"

# OpenAI 模型名 -> MIMO 模型名（与独立 claw_proxy 行为一致）
MODEL_MAPPING = {
    "gpt-3.5-turbo": "mimo-gpt-3.5-turbo",
    "gpt-4": "mimo-gpt-4",
    "gpt-4-turbo": "mimo-gpt-4-turbo",
}


def apply_model_mapping(data: Any) -> Any:
    """若 body 含 model 字段，则按 MODEL_MAPPING 映射；原地修改 dict。"""
    if not isinstance(data, dict):
        logger.warning("apply_model_mapping: 输入非 dict，跳过映射")
        return data
    if "model" in data:
        orig = data["model"]
        data["model"] = MODEL_MAPPING.get(orig, orig)
        logger.debug("模型映射: %s -> %s", orig, data["model"])
    return data


def build_mimo_json_headers(api_key: Optional[str]) -> Dict[str, str]:
    """发往 MIMO JSON API 的请求头（与内嵌 /v1 一致，避免盲目转发客户端杂头）。"""
    if not api_key:
        logger.warning("build_mimo_json_headers: api_key 为空")
    return {
        "Authorization": f"Bearer {api_key or ''}",
        "Content-Type": "application/json",
        "User-Agent": "MIMO-Proxy/1.0",
    }


def transform_mimo_response_json(mimo_response) -> Any:
    """从 requests.Response 解析为 dict 或原始文本。"""
    try:
        return mimo_response.json()
    except (ValueError, TypeError) as e:
        logger.debug("响应非 JSON: %s", e)
        return mimo_response.text


def chat_completion_log_summary(data: Any) -> Dict[str, Any]:
    """日志用摘要：模型、消息条数、内容总字符数（不含消息全文）。"""
    if not isinstance(data, dict):
        return {"note": "non-dict body", "type": type(data).__name__}
    msgs = data.get("messages")
    n = 0
    total_chars = 0
    if isinstance(msgs, list):
        n = len(msgs)
        for m in msgs:
            if not isinstance(m, dict):
                continue
            content = m.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                # 多模态 content 格式
                total_chars += sum(
                    len(part.get("text", ""))
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
    return {
        "model": data.get("model"),
        "message_count": n,
        "approx_total_chars": total_chars,
    }
