"""FastAPI 应用入口"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from src.cache.response import get_response_cache, init_cache
from src.config import get_config, reload_config
from src.proxy import router as proxy_router
from src.router.key_pool import init_key_pool, reset_key_pool
from src.router.model_router import reset_model_router

CONFIG_PATH = "config.yaml"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
log = logging.getLogger("deepseek-proxy")

_cleanup_task: asyncio.Task | None = None
_watcher_task: asyncio.Task | None = None


async def _periodic_cleanup():
    """每小时清理一次过期缓存"""
    while True:
        await asyncio.sleep(3600)
        try:
            cache = get_response_cache()
            await cache.cleanup()
        except Exception as e:
            log.warning(f"缓存清理异常: {e}")


async def _config_watcher():
    """监控 config.yaml 文件修改时间, 变化时自动热重载"""
    try:
        last_mtime = Path(CONFIG_PATH).stat().st_mtime if Path(CONFIG_PATH).exists() else 0
    except OSError:
        last_mtime = 0
    while True:
        await asyncio.sleep(3)
        try:
            if not Path(CONFIG_PATH).exists():
                continue
            mtime = Path(CONFIG_PATH).stat().st_mtime
            if mtime > last_mtime:
                last_mtime = mtime
                await _reload_all()
        except Exception as e:
            log.warning(f"配置文件监控异常: {e}")


async def _reload_all():
    """重新加载配置并重置所有模块"""
    try:
        reload_config(CONFIG_PATH)

        reset_key_pool()
        reset_model_router()

        cfg = get_config()
        if cfg.deepseek.api_keys:
            await init_key_pool()

        log.info("配置热重载完成: API Keys=%d, force_model=%s",
                 len(cfg.deepseek.api_keys), cfg.routing.force_model)
    except Exception as e:
        log.error(f"配置热重载失败: {e}", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _cleanup_task, _watcher_task
    cfg = get_config()
    Path(cfg.cache.db_path).parent.mkdir(parents=True, exist_ok=True)

    await init_cache()
    await init_key_pool()

    _cleanup_task = asyncio.create_task(_periodic_cleanup())
    _watcher_task = asyncio.create_task(_config_watcher())
    log.info("DeepSeek Proxy 启动完成 (配置文件热更新已启用)")
    yield

    if _cleanup_task:
        _cleanup_task.cancel()
    if _watcher_task:
        _watcher_task.cancel()
    cache = get_response_cache()
    await cache.close()
    log.info("DeepSeek Proxy 关闭完成")


def create_app() -> FastAPI:
    cfg = get_config()
    app = FastAPI(
        title=cfg.server.title,
        description=cfg.server.description,
        lifespan=lifespan,
    )

    app.include_router(proxy_router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()


if __name__ == "__main__":
    cfg = get_config()
    uvicorn.run(
        "src.main:app",
        host=cfg.server.host,
        port=cfg.server.port,
        reload=False,
    )