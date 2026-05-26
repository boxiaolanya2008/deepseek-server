"""统计追踪: 每次请求写入 SQLite, 支持费用/命中率/节省金额统计"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

from src.config import get_config

log = logging.getLogger("stats.tracker")


class StatsTracker:
    def __init__(self, db_path: str, model_pricing: Dict[str, Dict[str, float]]):
        self.db_path = db_path
        self.model_pricing = model_pricing
        self._db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    async def init(self):
        async with self._lock:
            self._db = await aiosqlite.connect(self.db_path, timeout=30)
            await self._db.execute("PRAGMA journal_mode=WAL")
            await self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS request_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    cache_hit_tokens INTEGER DEFAULT 0,
                    cache_miss_tokens INTEGER DEFAULT 0,
                    proxy_cache_hit INTEGER DEFAULT 0,
                    cost_usd REAL DEFAULT 0.0,
                    theoretical_cost_usd REAL DEFAULT 0.0
                )
                """
            )
            await self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_created_at ON request_stats(created_at)"
            )
            await self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_model ON request_stats(model)"
            )
            await self._db.commit()

    def _compute_cost(
        self,
        model: str,
        cache_hit_tokens: int,
        cache_miss_tokens: int,
        completion_tokens: int,
    ) -> tuple[float, float]:
        """计算实际费用和无缓存理论费用"""
        pricing = self.model_pricing.get(model, {
            "input_per_million": 0.14,
            "cache_hit_input_per_million": 0.0028,
            "output_per_million": 0.28,
        })
        cache_hit_cost = cache_hit_tokens / 1_000_000 * pricing["cache_hit_input_per_million"]
        cache_miss_cost = cache_miss_tokens / 1_000_000 * pricing["input_per_million"]
        output_cost = completion_tokens / 1_000_000 * pricing["output_per_million"]

        actual = cache_hit_cost + cache_miss_cost + output_cost
        theoretical = (cache_hit_tokens + cache_miss_tokens) / 1_000_000 * pricing["input_per_million"] + output_cost
        return round(actual, 6), round(theoretical, 6)

    async def record_request(
        self,
        model: str,
        cache_hit: bool,
        proxy_cache_hit: bool,
        prompt_tokens: int,
        completion_tokens: int,
        cache_hit_tokens: int,
        cache_miss_tokens: int,
    ):
        """记录单条请求统计"""
        async with self._lock:
            if self._db is None:
                return

        actual_cost, theoretical_cost = self._compute_cost(
            model, cache_hit_tokens, cache_miss_tokens, completion_tokens
        )

        # 代理层缓存命中: 实际费用 = 0
        if proxy_cache_hit:
            actual_cost = 0.0

        now = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            if self._db:
                await self._db.execute(
                    """
                    INSERT INTO request_stats
                    (created_at, model, prompt_tokens, completion_tokens,
                     cache_hit_tokens, cache_miss_tokens, proxy_cache_hit,
                     cost_usd, theoretical_cost_usd)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now, model, prompt_tokens, completion_tokens,
                        cache_hit_tokens, cache_miss_tokens,
                        1 if proxy_cache_hit else 0,
                        actual_cost, theoretical_cost,
                    ),
                )
                await self._db.commit()

    async def get_summary(self) -> Dict[str, Any]:
        """返回全局统计摘要"""
        async with self._lock:
            if self._db is None:
                return self._empty_summary()
            cur = await self._db.execute(
                """
                SELECT
                    COUNT(*) as total_requests,
                    COALESCE(SUM(prompt_tokens), 0) as total_prompt,
                    COALESCE(SUM(completion_tokens), 0) as total_completion,
                    COALESCE(SUM(cache_hit_tokens), 0) as total_cache_hit,
                    COALESCE(SUM(cache_miss_tokens), 0) as total_cache_miss,
                    COALESCE(SUM(cost_usd), 0) as total_cost,
                    COALESCE(SUM(theoretical_cost_usd), 0) as total_theoretical,
                    COALESCE(SUM(proxy_cache_hit), 0) as proxy_cache_hits
                FROM request_stats
                """
            )
            row = await cur.fetchone()
            total_req = row[0] or 0
            total_cache_hit = row[3] or 0
            total_prompt = row[1] or 0
            total_cache_miss = row[4] or 0
            total_cache = total_cache_hit + total_cache_miss
            deepseek_hit_rate = round(total_cache_hit / total_cache * 100, 2) if total_cache > 0 else 0.0
            total_cost = row[5] or 0.0
            total_theoretical = row[6] or 0.0
            proxy_cache_hits = row[7] or 0

            return {
                "total_requests": total_req,
                "total_prompt_tokens": total_prompt,
                "total_completion_tokens": row[2] or 0,
                "deepseek_cache_hit_rate": deepseek_hit_rate,
                "deepseek_cache_hit_tokens": total_cache_hit,
                "deepseek_cache_miss_tokens": total_cache_miss,
                "proxy_cache_hits": proxy_cache_hits,
                "total_cost_usd": round(total_cost, 4),
                "total_theoretical_cost_usd": round(total_theoretical, 4),
                "total_saved_usd": round(total_theoretical - total_cost, 4),
            }

    async def get_daily(self, days: int = 30) -> List[Dict[str, Any]]:
        """返回最近 N 天的每日统计"""
        async with self._lock:
            if self._db is None:
                return []
            cur = await self._db.execute(
                """
                SELECT
                    DATE(created_at) as day,
                    COUNT(*) as requests,
                    SUM(prompt_tokens) as prompt,
                    SUM(completion_tokens) as completion,
                    SUM(cache_hit_tokens) as cache_hit,
                    SUM(cache_miss_tokens) as cache_miss,
                    SUM(cost_usd) as cost,
                    SUM(theoretical_cost_usd) as theoretical,
                    SUM(proxy_cache_hit) as proxy_hits
                FROM request_stats
                WHERE created_at >= DATE('now', ?)
                GROUP BY DATE(created_at)
                ORDER BY day ASC
                """,
                (f"-{days} days",),
            )
            rows = await cur.fetchall()
            return [
                {
                    "date": r[0],
                    "requests": r[1],
                    "prompt_tokens": r[2] or 0,
                    "completion_tokens": r[3] or 0,
                    "cache_hit_tokens": r[4] or 0,
                    "cache_miss_tokens": r[5] or 0,
                    "cost_usd": round(r[6] or 0.0, 4),
                    "theoretical_cost_usd": round(r[7] or 0.0, 4),
                    "saved_usd": round((r[7] or 0.0) - (r[6] or 0.0), 4),
                    "proxy_cache_hits": r[8] or 0,
                }
                for r in rows
            ]

    async def get_by_model(self) -> List[Dict[str, Any]]:
        """按模型分组的统计"""
        async with self._lock:
            if self._db is None:
                return []
            cur = await self._db.execute(
                """
                SELECT
                    model,
                    COUNT(*) as requests,
                    SUM(prompt_tokens) as prompt,
                    SUM(completion_tokens) as completion,
                    SUM(cache_hit_tokens) as cache_hit,
                    SUM(cache_miss_tokens) as cache_miss,
                    SUM(cost_usd) as cost,
                    SUM(theoretical_cost_usd) as theoretical
                FROM request_stats
                GROUP BY model
                ORDER BY cost DESC
                """
            )
            rows = await cur.fetchall()
            return [
                {
                    "model": r[0],
                    "requests": r[1],
                    "prompt_tokens": r[2] or 0,
                    "completion_tokens": r[3] or 0,
                    "cache_hit_tokens": r[4] or 0,
                    "cache_miss_tokens": r[5] or 0,
                    "cost_usd": round(r[6] or 0.0, 4),
                    "theoretical_cost_usd": round(r[7] or 0.0, 4),
                    "saved_usd": round((r[7] or 0.0) - (r[6] or 0.0), 4),
                }
                for r in rows
            ]

    async def close(self):
        async with self._lock:
            if self._db:
                await self._db.close()
                self._db = None

    def _empty_summary(self) -> Dict[str, Any]:
        return {
            "total_requests": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "deepseek_cache_hit_rate": 0.0,
            "deepseek_cache_hit_tokens": 0,
            "deepseek_cache_miss_tokens": 0,
            "proxy_cache_hits": 0,
            "total_cost_usd": 0.0,
            "total_theoretical_cost_usd": 0.0,
            "total_saved_usd": 0.0,
        }


# 全局单例
_tracker: Optional[StatsTracker] = None


def get_stats_tracker() -> StatsTracker:
    global _tracker
    if _tracker is None:
        cfg = get_config()
        pricing_dict = {}
        for name, p in cfg.pricing.model_pricing.items():
            pricing_dict[name] = {
                "input_per_million": p.input_per_million,
                "cache_hit_input_per_million": p.cache_hit_input_per_million,
                "output_per_million": p.output_per_million,
            }
        _tracker = StatsTracker(
            db_path=cfg.stats.db_path,
            model_pricing=pricing_dict,
        )
    return _tracker


async def init_stats():
    tracker = get_stats_tracker()
    await tracker.init()


def reset_stats_tracker():
    """重置统计追踪器, 下次 get_stats_tracker() 时从新配置重新创建"""
    global _tracker
    if _tracker is not None:
        import asyncio
        try:
            asyncio.get_event_loop().run_until_complete(_tracker.close())
        except Exception:
            pass
    _tracker = None