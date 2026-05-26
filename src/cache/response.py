"""响应缓存: 基于 hash 的 SQLite 精确匹配缓存"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

from src.config import get_config

log = logging.getLogger("cache.response")


@dataclass
class CacheEntry:
    cache_key: str
    response_body: bytes
    created_at: float
    last_accessed: float
    access_count: int


def _compute_cache_key(
    model: str,
    messages: List[Dict[str, Any]],
    params: Dict[str, Any],
) -> str:
    """计算请求的唯一 hash key"""
    key_payload = {
        "model": model,
        "messages": messages,
        "temperature": params.get("temperature"),
        "top_p": params.get("top_p"),
        "max_tokens": params.get("max_tokens"),
        "stop": params.get("stop"),
    }
    raw = json.dumps(key_payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ResponseCache:
    def __init__(self, db_path: str, ttl_hours: int = 24, max_entries: int = 10000):
        self.db_path = db_path
        self.ttl_seconds = ttl_hours * 3600
        self.max_entries = max_entries
        self._db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    async def init(self):
        """初始化数据库表"""
        async with self._lock:
            self._db = await aiosqlite.connect(self.db_path, timeout=30)
            await self._db.execute("PRAGMA journal_mode=WAL")
            await self._db.execute("PRAGMA synchronous=NORMAL")
            await self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS response_cache (
                    cache_key TEXT PRIMARY KEY,
                    response_body BLOB NOT NULL,
                    model TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_accessed REAL NOT NULL,
                    access_count INTEGER DEFAULT 1
                )
                """
            )
            await self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_created_at ON response_cache(created_at)"
            )
            await self._db.commit()

    async def get(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        params: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """查询缓存, 命中则返回响应体, 否则返回 None"""
        key = _compute_cache_key(model, messages, params)
        async with self._lock:
            if self._db is None:
                return None
            now = time.time()
            cursor = await self._db.execute(
                "SELECT response_body, last_accessed, access_count FROM response_cache WHERE cache_key = ?",
                (key,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            # 检查 TTL
            if now - row[0] > self.ttl_seconds:
                await self._db.execute("DELETE FROM response_cache WHERE cache_key = ?", (key,))
                await self._db.commit()
                return None
            # 更新访问时间和次数
            await self._db.execute(
                "UPDATE response_cache SET last_accessed = ?, access_count = access_count + 1 WHERE cache_key = ?",
                (now, key),
            )
            await self._db.commit()
            body = json.loads(row[0].decode("utf-8"))
            log.debug(f"缓存命中: {key[:8]}...")
            return body

    async def set(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        params: Dict[str, Any],
        response_body: Dict[str, Any],
    ) -> None:
        """写入缓存"""
        key = _compute_cache_key(model, messages, params)
        now = time.time()
        body_bytes = json.dumps(response_body, ensure_ascii=False).encode("utf-8")
        async with self._lock:
            if self._db is None:
                return
            await self._db.execute(
                """
                INSERT OR REPLACE INTO response_cache
                (cache_key, response_body, model, created_at, last_accessed, access_count)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (key, body_bytes, model, now, now),
            )
            await self._db.commit()
        log.debug(f"缓存写入: {key[:8]}...")

    async def cleanup(self) -> int:
        """清理过期条目和超限条目, 返回删除数量"""
        async with self._lock:
            if self._db is None:
                return 0
            now = time.time()
            cursor = await self._db.execute(
                "SELECT COUNT(*) FROM response_cache"
            )
            total = (await cursor.fetchone())[0]
            deleted = 0
            # 删除过期
            cur = await self._db.execute(
                "DELETE FROM response_cache WHERE ? - created_at > ?",
                (now, self.ttl_seconds),
            )
            deleted += cur.rowcount
            # 超量: 删除最旧的
            if total - deleted > self.max_entries:
                excess = total - deleted - self.max_entries
                cur2 = await self._db.execute(
                    """
                    DELETE FROM response_cache WHERE cache_key IN (
                        SELECT cache_key FROM response_cache
                        ORDER BY last_accessed ASC
                        LIMIT ?
                    )
                    """,
                    (excess,),
                )
                deleted += cur2.rowcount
            await self._db.commit()
            if deleted > 0:
                log.info(f"缓存清理: 删除了 {deleted} 条过期/超限条目")
            return deleted

    async def get_stats(self) -> Dict[str, Any]:
        """返回缓存统计"""
        async with self._lock:
            if self._db is None:
                return {"total": 0, "size_mb": 0.0}
            cursor = await self._db.execute("SELECT COUNT(*), SUM(LENGTH(response_body)) FROM response_cache")
            row = await cursor.fetchone()
            total = row[0] or 0
            size_bytes = row[1] or 0
            return {"total": total, "size_mb": round(size_bytes / 1024 / 1024, 3)}

    async def close(self):
        async with self._lock:
            if self._db:
                await self._db.close()
                self._db = None


# 全局单例
_cache: Optional[ResponseCache] = None


def get_response_cache() -> ResponseCache:
    global _cache
    if _cache is None:
        cfg = get_config()
        _cache = ResponseCache(
            db_path=cfg.cache.db_path,
            ttl_hours=cfg.cache.ttl_hours,
            max_entries=cfg.cache.max_entries,
        )
    return _cache


async def init_cache():
    cache = get_response_cache()
    await cache.init()