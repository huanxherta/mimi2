#!/usr/bin/env python3
"""
OpenAI 协议中转服务
将OpenAI API请求中转到小米MIMO API
"""

import logging
import os
import sys
import json
import time

import requests
from flask import Flask, request, jsonify, Response, stream_with_context

# 导入配置
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claw_web
from mimo_openai_shared import (
    MIMO_BASE_URL,
    apply_model_mapping,
    build_mimo_json_headers,
    transform_mimo_response_json,
)

logger = logging.getLogger(__name__)

# 子进程仅 import 本模块，需从磁盘同步主进程已写入的密钥（与内存隔离问题对齐）
claw_web.sync_mimo_key_from_app_state()

app = Flask(__name__)


def transform_request(openai_request):
    """将OpenAI请求转换为MIMO请求"""
    raw = openai_request.get_json()
    if not isinstance(raw, dict):
        logger.warning("transform_request: 请求体非 JSON dict")
        return {}, {}
    data = dict(raw)
    apply_model_mapping(data)
    headers = build_mimo_json_headers(claw_web.mimo_api_key)
    return data, headers


@app.route('/v1/chat/completions', methods=['POST'])
def chat_completions():
    """中转聊天完成请求"""
    if not claw_web.ensure_openai_proxy_auth():
        return jsonify({
            "error": {
                "message": "MIMO API key is not available or expired",
                "type": "authentication_error"
            }
        }), 401

    try:
        data, headers = transform_request(request)

        if data.get("stream"):
            url = f"{MIMO_BASE_URL}/v1/chat/completions"
            r = requests.post(url, json=data, headers=headers, timeout=120, stream=True)
            logger.info("MIMO API流式响应状态: %s", r.status_code)
            if r.status_code != 200:
                err = r.content
                ct = r.headers.get("content-type", "application/json")
                st = r.status_code
                r.close()
                return Response(err, status=st, content_type=ct)
            ct = r.headers.get("content-type", "text/event-stream; charset=utf-8")

            def gen():
                try:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            yield chunk
                finally:
                    r.close()

            return Response(stream_with_context(gen()), status=200, content_type=ct)

        # 非流式
        response = requests.post(
            f"{MIMO_BASE_URL}/v1/chat/completions",
            json=data,
            headers=headers,
            timeout=120
        )

        transformed_data = transform_mimo_response_json(response)

        return Response(
            json.dumps(transformed_data),
            status=response.status_code,
            content_type=response.headers.get('content-type', 'application/json')
        )

    except requests.RequestException as e:
        logger.error("中转请求异常: %s", e, exc_info=True)
        return jsonify({
            "error": {
                "message": f"Proxy error: {e}",
                "type": "proxy_error"
            }
        }), 502
    except Exception as e:
        logger.error("中转未知异常: %s", e, exc_info=True)
        return jsonify({
            "error": {
                "message": f"Internal error: {e}",
                "type": "internal_error"
            }
        }), 500


@app.route('/v1/models', methods=['GET'])
def list_models():
    """中转模型列表请求"""
    if not claw_web.ensure_openai_proxy_auth():
        return jsonify({
            "error": {
                "message": "MIMO API key is not available or expired",
                "type": "authentication_error"
            }
        }), 401

    try:
        headers = build_mimo_json_headers(claw_web.mimo_api_key)
        response = requests.get(
            f"{MIMO_BASE_URL}/v1/models",
            headers=headers,
            timeout=30
        )
        transformed_data = transform_mimo_response_json(response)
        return Response(
            json.dumps(transformed_data),
            status=response.status_code,
            content_type=response.headers.get('content-type', 'application/json')
        )
    except requests.RequestException as e:
        logger.error("模型列表请求异常: %s", e, exc_info=True)
        return jsonify({
            "error": {
                "message": f"Proxy error: {e}",
                "type": "proxy_error"
            }
        }), 502
    except Exception as e:
        logger.error("模型列表未知异常: %s", e, exc_info=True)
        return jsonify({
            "error": {
                "message": f"Internal error: {e}",
                "type": "internal_error"
            }
        }), 500


@app.route('/health', methods=['GET'])
def health_check():
    """健康检查（无密钥时不触发完整 Claw 刷新链）"""
    if not claw_web.mimo_api_key:
        claw_web.sync_mimo_key_from_app_state()
    key_valid = bool(claw_web.mimo_api_key and claw_web.refresh_key_if_needed())
    return jsonify({
        "status": "healthy" if key_valid else "unhealthy",
        "mimo_key_available": bool(claw_web.mimo_api_key),
        "key_valid": key_valid,
        "timestamp": int(time.time())
    })


@app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS'])
def proxy_all(path):
    """通用中转路由"""
    if not claw_web.ensure_openai_proxy_auth():
        return jsonify({
            "error": {
                "message": "MIMO API key is not available or expired",
                "type": "authentication_error"
            }
        }), 401

    try:
        headers = dict(request.headers)
        headers['Authorization'] = f'Bearer {claw_web.mimo_api_key}'
        headers.pop('Host', None)
        headers.pop('Content-Length', None)
        headers.pop('Transfer-Encoding', None)

        url = f"{MIMO_BASE_URL}/{path}"

        if request.method in ['POST', 'PUT', 'PATCH']:
            response = requests.request(
                request.method,
                url,
                json=request.get_json(silent=True) if request.is_json else None,
                data=request.get_data() if not request.is_json else None,
                headers=headers,
                params=request.args,
                timeout=120
            )
        else:
            response = requests.request(
                request.method,
                url,
                headers=headers,
                params=request.args,
                timeout=120
            )

        transformed_data = transform_mimo_response_json(response)

        return Response(
            json.dumps(transformed_data) if isinstance(transformed_data, (dict, list)) else str(transformed_data),
            status=response.status_code,
            content_type=response.headers.get('content-type', 'application/json')
        )

    except requests.RequestException as e:
        logger.error("通用中转请求异常 [%s /%s]: %s", request.method, path, e, exc_info=True)
        return jsonify({
            "error": {
                "message": f"Proxy error: {e}",
                "type": "proxy_error"
            }
        }), 502
    except Exception as e:
        logger.error("通用中转未知异常 [%s /%s]: %s", request.method, path, e, exc_info=True)
        return jsonify({
            "error": {
                "message": f"Internal error: {e}",
                "type": "internal_error"
            }
        }), 500


def run_proxy():
    """运行代理服务"""
    logger.info("启动OpenAI协议中转服务...")
    app.run(host='0.0.0.0', port=8000, debug=False, threaded=True)


if __name__ == '__main__':
    run_proxy()
