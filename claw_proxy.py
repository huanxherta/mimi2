#!/usr/bin/env python3
"""
OpenAI 协议中转服务
将OpenAI API请求中转到小米MIMO API
"""

import os
import json
import time
import requests
from flask import Flask, request, jsonify, Response, stream_with_context
import sys

# 导入配置
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claw_web
from mimo_openai_shared import (
    MIMO_BASE_URL,
    apply_model_mapping,
    build_mimo_json_headers,
    transform_mimo_response_json,
)

# 子进程仅 import 本模块，需从磁盘同步主进程已写入的密钥（与内存隔离问题对齐）
claw_web.sync_mimo_key_from_app_state()

app = Flask(__name__)


def transform_request(openai_request):
    """将OpenAI请求转换为MIMO请求"""
    raw = openai_request.get_json()
    data = dict(raw) if isinstance(raw, dict) else {}
    apply_model_mapping(data)
    headers = build_mimo_json_headers(claw_web.mimo_api_key)
    return data, headers


def transform_response(mimo_response):
    """将MIMO响应转换为OpenAI格式"""
    return transform_mimo_response_json(mimo_response)


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
        # 转换请求
        data, headers = transform_request(request)

        if data.get("stream"):
            url = f"{MIMO_BASE_URL}/v1/chat/completions"
            r = requests.post(url, json=data, headers=headers, timeout=120, stream=True)
            log_st = f"MIMO API流式响应状态: {r.status_code}"
            print(log_st, file=sys.stderr)
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

        # 发送到MIMO API（非流式）
        response = requests.post(
            f"{MIMO_BASE_URL}/v1/chat/completions",
            json=data,
            headers=headers,
            timeout=120
        )

        # 转换响应
        transformed_data = transform_response(response)

        # 返回响应
        return Response(
            json.dumps(transformed_data),
            status=response.status_code,
            content_type=response.headers.get('content-type', 'application/json')
        )

    except Exception as e:
        return jsonify({
            "error": {
                "message": f"Proxy error: {str(e)}",
                "type": "proxy_error"
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

        transformed_data = transform_response(response)

        return Response(
            json.dumps(transformed_data),
            status=response.status_code,
            content_type=response.headers.get('content-type', 'application/json')
        )

    except Exception as e:
        return jsonify({
            "error": {
                "message": f"Proxy error: {str(e)}",
                "type": "proxy_error"
            }
        }), 500


@app.route('/health', methods=['GET'])
def health_check():
    """健康检查（与原先一致：无密钥时不触发完整 Claw 刷新链）"""
    if not claw_web.mimo_api_key:
        claw_web.sync_mimo_key_from_app_state()
    key_valid = bool(claw_web.mimo_api_key and claw_web.refresh_key_if_needed())
    return jsonify({
        "status": "healthy" if key_valid else "unhealthy",
        "mimo_key_available": bool(claw_web.mimo_api_key),
        "key_valid": key_valid,
        "timestamp": int(time.time())
    })


# 通用的中转路由
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

        # 移除可能导致问题的头部
        headers.pop('Host', None)

        url = f"{MIMO_BASE_URL}/{path}"

        # 根据请求方法处理
        if request.method in ['POST', 'PUT', 'PATCH']:
            response = requests.request(
                request.method,
                url,
                json=request.get_json() if request.is_json else None,
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

        transformed_data = transform_response(response)

        return Response(
            json.dumps(transformed_data) if isinstance(transformed_data, dict) else transformed_data,
            status=response.status_code,
            content_type=response.headers.get('content-type', 'application/json')
        )

    except Exception as e:
        return jsonify({
            "error": {
                "message": f"Proxy error: {str(e)}",
                "type": "proxy_error"
            }
        }), 500


def run_proxy():
    """运行代理服务"""
    print("启动OpenAI协议中转服务...", file=sys.stderr)
    app.run(host='0.0.0.0', port=8000, debug=False, threaded=True)


if __name__ == '__main__':
    run_proxy()
