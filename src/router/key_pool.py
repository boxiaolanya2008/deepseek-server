"""API Key 轮询池: 支持 Round-Robin + 429 退避 + 强制回退"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import httpx

from src.config import get_config

log = logging.getLogger("router.key_pool")


@dataclass
class KeyStatus:
    key: str
    available: bool = True
    backoff_until: float = 0.0
    error_count: int = 0
    last_used: float = 0.0

    def is_ready(self) -> bool:
        return self.available and time.time() >= self.backoff_until


class KeyPool:
    def __init__(self, keys: List[str], backoff_seconds: int = 60):
        self._keys: List[KeyStatus] = [KeyStatus(key=k) for k in keys]
        self._index = 0
        self._lock = asyncio.Lock()
        self._backoff_seconds = backoff_seconds

    def get_next_key(self) -> str:
        """返回下一个可用的 key。所有 key 都不可用时，强制选择最早的一个恢复使用。"""
        ready = [k for k in self._keys if k.is_ready()]
        if ready:
            n = len(ready)
            start = self._index % n
            ks = ready[start]
            ks.last_used = time.time()
            self._index += 1
            return ks.key

        # 所有 key 都在退避: 选 error_count 最小 + backoff_until 最早的
        best = min(self._keys, key=lambda k: (k.error_count, k.backoff_until))
        log.warning(f"所有 Key 不可用, 强制使用 {best.key[:8]}... (退避剩余 {max(0, best.backoff_until - time.time()):.0f}s)")
        best.last_used = time.time()
        self._index += 1
        return best.key

    def mark_error(self, key: str, is_rate_limit: bool = False):
        """标记 key 失败, rate limit 触发退避"""
        for ks in self._keys:
            if ks.key == key:
                ks.error_count += 1
                if is_rate_limit:
                    delay = min(self._backoff_seconds * (2 ** min(ks.error_count - 1, 3)), 300)
                    ks.backoff_until = time.time() + delay
                    ks.available = False
                    log.warning(f"Key {key[:8]}... RateLimit, 退避 {delay:.0f}s (第{ks.error_count}次)")
                else:
                    ks.available = False
                    log.warning(f"Key {key[:8]}... 不可用 (错误 #{ks.error_count})")
                break

    def mark_success(self, key: str):
        """标记 key 请求成功, 立即恢复"""
        for ks in self._keys:
            if ks.key == key:
                ks.available = True
                ks.error_count = 0
                ks.backoff_until = 0.0
                break

    async def health_check(self, base_url: str):
        """用轻量请求验证所有 key 有效性"""
        for ks in self._keys:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        f"{base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {ks.key}"},
                        json={
                            "model": "deepseek-v4-flash",
                            "messages": [{"role": "user", "content": "hi"}],
                            "max_tokens": 1,
                        },
                    )
                    if resp.status_code == 200:
                        ks.available = True
                        ks.error_count = 0
                        log.info(f"Key {ks.key[:8]}... 健康检查通过")
                    else:
                        ks.available = False
                        log.warning(f"Key {ks.key[:8]}... 健康检查失败: {resp.status_code}")
            except Exception as e:
                ks.available = False
                log.warning(f"Key {ks.key[:8]}... 健康检查异常: {e}")

    def get_stats(self) -> Dict:
        return {
            "total": len(self._keys),
            "available": sum(1 for k in self._keys if k.is_ready()),
            "backing_off": sum(1 for k in self._keys if not k.is_ready()),
        }


# 全局单例
_pool: Optional[KeyPool] = None


def get_key_pool() -> KeyPool:
    global _pool
    if _pool is None:
        cfg = get_config()
        keys = cfg.deepseek.api_keys
        if not keys:
            raise ValueError("未配置 DeepSeek API Keys，请检查 config.yaml 或 .env")
        _pool = KeyPool(
            keys=keys,
            backoff_seconds=cfg.key_pool.backoff_seconds,
        )
    return _pool


async def init_key_pool():
    cfg = get_config()
    pool = get_key_pool()
    if pool._keys and cfg.key_pool.health_check_interval > 0:
        await pool.health_check(cfg.deepseek.base_url)


def reset_key_pool():
    """重置 Key 池, 下次 get_key_pool() 时从新配置重新创建"""
    global _pool
    _pool = None