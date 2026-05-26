"""核心代理: /v1/chat/completions 路由, 串联缓存 + 路由 + 统计"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Union

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.cache.prefix import optimize_messages
from src.cache.response import get_response_cache
from src.config import get_config
from src.router.key_pool import get_key_pool
from src.router.model_router import get_model_router
from src.stats.tracker import get_stats_tracker

log = logging.getLogger("proxy")
router = APIRouter(prefix="/v1", tags=["proxy"])

CHAT_COMPLETIONS = "/chat/completions"

AVAILABLE_MODELS = [
    {"id": "deepseek-v4-flash", "object": "model", "created": 1714000000, "owned_by": "deepseek", "permission": [], "root": "deepseek-v4-flash", "parent": None},
    {"id": "deepseek-v4-pro", "object": "model", "created": 1714000000, "owned_by": "deepseek", "permission": [], "root": "deepseek-v4-pro", "parent": None},
    {"id": "deepseek-chat", "object": "model", "created": 1714000000, "owned_by": "deepseek", "permission": [], "root": "deepseek-chat", "parent": None},
    {"id": "deepseek-reasoner", "object": "model", "created": 1714000000, "owned_by": "deepseek", "permission": [], "root": "deepseek-reasoner", "parent": None},
]

# 模块级 httpx 客户端
_http_client: Optional[httpx.AsyncClient] = None


def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
        )
    return _http_client


@router.get("/models")
async def list_models():
    """OpenAI 兼容的模型列表端点"""
    cfg = get_config()
    alias_models = [
        {"id": name, "object": "model", "created": 1714000000, "owned_by": "deepseek", "permission": [], "root": name, "parent": None}
        for name in cfg.deepseek.model_aliases.keys()
    ]
    return {"object": "list", "data": AVAILABLE_MODELS + alias_models}


@router.get("")
async def v1_root():
    """GET /v1 根路径"""
    return {"object": "list", "data": AVAILABLE_MODELS}


def _extract_messages(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    return body.get("messages", [])


def _build_proxy_body(body: Dict[str, Any], actual_model: str) -> Dict[str, Any]:
    proxy_body = dict(body)
    proxy_body["model"] = actual_model
    return proxy_body


def _sse_chunk(text: str) -> bytes:
    """将文本包装为 SSE data: 格式"""
    return f"data: {text}\n\n".encode("utf-8")


def _response_to_sse_stream(result: Dict[str, Any]) -> AsyncIterator[bytes]:
    """
    将完整响应转换为 SSE 流式格式:
    - 先发送 choices 中每个 delta
    - 最后发送带 usage 的结束块和 [DONE]
    """
    choices = result.get("choices", [])
    model = result.get("model", "")
    resp_id = result.get("id", "")
    created = result.get("created", 0)
    usage = result.get("usage", {})

    # 逐 choice 发送 delta
    for choice in choices:
        delta = choice.get("message", choice.get("delta", {}))
        if delta:
            chunk = {
                "id": resp_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{
                    "index": choice.get("index", 0),
                    "delta": delta,
                    "finish_reason": None,
                }],
            }
            yield _sse_chunk(json.dumps(chunk, ensure_ascii=False))

    # 发送结束块 (finish_reason + usage)
    finish_chunk = {
        "id": resp_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {},
            "finish_reason": "stop",
        }],
    }
    if usage:
        finish_chunk["usage"] = usage
    yield _sse_chunk(json.dumps(finish_chunk, ensure_ascii=False))
    yield b"data: [DONE]\n\n"


def _cached_stream_response(raw_sse_bytes: bytes) -> AsyncIterator[bytes]:
    """将缓存的原始 SSE 字节流逐行返回"""
    for line in raw_sse_bytes.split(b"\n"):
        if line.strip():
            yield line + b"\n"
        else:
            yield b"\n"


@router.api_route("/chat/completions", methods=["POST"])
async def chat_completions(request: Request):
    """OpenAI 兼容的 /v1/chat/completions 端点"""
    cfg = get_config()
    body = await request.json()

    model = body.get("model", cfg.routing.default_model)
    messages = _extract_messages(body)
    is_stream = body.get("stream", False)
    use_cache = cfg.cache.enabled
    cache_stream = cfg.cache.cache_stream

    # 1. 前缀优化
    if messages:
        body["messages"] = optimize_messages(messages)

    cache_params = {
        "temperature": body.get("temperature"),
        "top_p": body.get("top_p"),
        "max_tokens": body.get("max_tokens"),
    }

    # 2. 代理层缓存检查 (非 stream 或 启用了 stream 缓存)
    if use_cache and (not is_stream or cache_stream):
        cache = get_response_cache()
        cached = await cache.get(model=model, messages=body["messages"], params=cache_params)
        if cached:
            tracker = get_stats_tracker()
            await tracker.record_request(
                model=model, cache_hit=True, proxy_cache_hit=True,
                prompt_tokens=0, completion_tokens=0, cache_hit_tokens=0, cache_miss_tokens=0,
            )
            log.info(f"[缓存命中] model={model}, stream={is_stream}")

            # 如果是 stream 请求但缓存了非 stream 结果, 转为 SSE 流返回
            if is_stream:
                return StreamingResponse(
                    _response_to_sse_stream(cached),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
            return cached

    # 3. 模型路由
    router_instance = get_model_router()
    actual_model = router_instance.resolve(model=model, messages=body["messages"])

    # 4. 获取 API Key
    key_pool = get_key_pool()
    api_key = key_pool.get_next_key()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = f"{cfg.deepseek.base_url}{CHAT_COMPLETIONS}"

    # 5. 如果是 stream 且启用了 stream 缓存, 以非 stream 方式请求以便缓存
    if is_stream and cache_stream:
        proxy_body = _build_proxy_body({k: v for k, v in body.items() if k not in ("stream", "stream_options")}, actual_model)
        proxy_body["stream"] = False

        log.info(f"[请求-stream->cached] model={model} -> actual={actual_model}")
        client = get_http_client()
        request_start = time.time()

        try:
            resp = await client.post(url, json=proxy_body, headers=headers, timeout=120.0)

            if resp.status_code != 200:
                err_text = resp.text
                log.error(f"DeepSeek API 错误: {resp.status_code} {err_text}")
                if resp.status_code == 429:
                    key_pool.mark_error(api_key, is_rate_limit=True)
                elif resp.status_code >= 500:
                    key_pool.mark_error(api_key, is_rate_limit=False)
                raise HTTPException(status_code=resp.status_code, detail=err_text)

            key_pool.mark_success(api_key)
            result = resp.json()

            # 写入缓存
            if use_cache:
                cache = get_response_cache()
                await cache.set(model=model, messages=body["messages"], params=cache_params, response_body=result)

            # 记录统计
            usage = result.get("usage", {})
            tracker = get_stats_tracker()
            await tracker.record_request(
                model=actual_model, cache_hit=False, proxy_cache_hit=False,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                cache_hit_tokens=usage.get("prompt_cache_hit_tokens", 0),
                cache_miss_tokens=usage.get("prompt_cache_miss_tokens", usage.get("prompt_tokens", 0)),
            )

            elapsed = time.time() - request_start
            log.info(f"[流式转换] model={actual_model}, prompt={usage.get('prompt_tokens',0)}, "
                     f"completion={usage.get('completion_tokens',0)}, elapsed={elapsed:.2f}s")

            # 将完整响应转为 SSE 流返回给客户端
            return StreamingResponse(
                _response_to_sse_stream(result),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        except httpx.TimeoutException as e:
            log.error(f"请求超时: {e}")
            key_pool.mark_error(api_key, is_rate_limit=False)
            raise HTTPException(status_code=504, detail="请求 DeepSeek 超时")
        except httpx.ConnectError as e:
            log.error(f"连接 DeepSeek 失败: {e}")
            key_pool.mark_error(api_key, is_rate_limit=False)
            raise HTTPException(status_code=502, detail=f"无法连接 DeepSeek: {e}")
        except HTTPException:
            raise
        except Exception as e:
            log.error(f"代理异常: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    # 6. 普通转发 (非 stream 或 stream 缓存未启用)
    proxy_body = _build_proxy_body(body, actual_model)
    log.info(f"[请求] model={model} -> actual={actual_model}, stream={is_stream}")

    client = get_http_client()
    request_start = time.time()

    try:
        resp = await client.post(url, json=proxy_body, headers=headers, timeout=120.0)

        if resp.status_code != 200:
            err_text = resp.text
            log.error(f"DeepSeek API 错误: {resp.status_code} {err_text}")
            if resp.status_code == 429:
                key_pool.mark_error(api_key, is_rate_limit=True)
            elif resp.status_code >= 500:
                key_pool.mark_error(api_key, is_rate_limit=False)
            raise HTTPException(status_code=resp.status_code, detail=err_text)

        key_pool.mark_success(api_key)

        if is_stream:
            # stream 且未启用 stream 缓存: 直接透传
            async def _passthrough():
                async for chunk in resp.aiter_bytes(chunk_size=None):
                    if chunk:
                        yield chunk

            return StreamingResponse(
                _passthrough(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        # 非 stream: 完整响应
        result = resp.json()

        if use_cache:
            cache = get_response_cache()
            await cache.set(model=model, messages=body["messages"], params=cache_params, response_body=result)

        usage = result.get("usage", {})
        tracker = get_stats_tracker()
        await tracker.record_request(
            model=actual_model, cache_hit=False, proxy_cache_hit=False,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            cache_hit_tokens=usage.get("prompt_cache_hit_tokens", 0),
            cache_miss_tokens=usage.get("prompt_cache_miss_tokens", usage.get("prompt_tokens", 0)),
        )

        elapsed = time.time() - request_start
        log.info(f"[完成] model={actual_model}, prompt={usage.get('prompt_tokens',0)}, "
                 f"completion={usage.get('completion_tokens',0)}, elapsed={elapsed:.2f}s")

        return result

    except httpx.TimeoutException as e:
        log.error(f"请求超时: {e}")
        key_pool.mark_error(api_key, is_rate_limit=False)
        raise HTTPException(status_code=504, detail="请求 DeepSeek 超时")
    except httpx.ConnectError as e:
        log.error(f"连接 DeepSeek 失败: {e}")
        key_pool.mark_error(api_key, is_rate_limit=False)
        raise HTTPException(status_code=502, detail=f"无法连接 DeepSeek: {e}")
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"代理异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))