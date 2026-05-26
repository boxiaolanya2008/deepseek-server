"""配置管理: 合并 YAML 文件配置与 .env 环境变量"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    title: str = "DeepSeek Proxy"
    description: str = ""


class DeepSeekConfig(BaseModel):
    base_url: str = "https://api.deepseek.com"
    api_keys: List[str] = Field(default_factory=list)
    model_aliases: Dict[str, str] = Field(default_factory=dict)


class CacheConfig(BaseModel):
    enabled: bool = True
    db_path: str = "./data/cache.db"
    ttl_hours: int = 24
    max_entries: int = 10000
    cache_stream: bool = False


class RoutingContentRule(BaseModel):
    """基于内容关键词的模型路由规则"""
    keywords: List[str] = Field(default_factory=list)
    model: str = ""


class RoutingConfig(BaseModel):
    default_model: str = "deepseek-v4-flash"
    force_model: str = ""
    content_rules: Dict[str, str] = Field(default_factory=dict)


class KeyPoolConfig(BaseModel):
    strategy: str = "round_robin"
    backoff_seconds: int = 60
    health_check_interval: int = 300


class ModelPricing(BaseModel):
    input_per_million: float = 0.14
    cache_hit_input_per_million: float = 0.0028
    output_per_million: float = 0.28


class PricingConfig(BaseModel):
    model_pricing: Dict[str, ModelPricing] = Field(default_factory=dict)


class StatsConfig(BaseModel):
    db_path: str = "./data/stats.db"


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    deepseek: DeepSeekConfig = Field(default_factory=DeepSeekConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    key_pool: KeyPoolConfig = Field(default_factory=KeyPoolConfig)
    pricing: PricingConfig = Field(default_factory=PricingConfig)
    stats: StatsConfig = Field(default_factory=StatsConfig)


def _flatten_yaml(data: Dict[str, Any]) -> Dict[str, Any]:
    """将嵌套 YAML 结构展平以便于 .env 覆盖"""
    result = {}

    def walk(obj: Any, prefix: str = ""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                new_key = f"{prefix}_{k}" if prefix else k
                if isinstance(v, dict):
                    walk(v, new_key)
                else:
                    result[new_key] = v
        elif isinstance(obj, list):
            result[prefix] = ",".join(str(x) for x in obj)

    walk(data)
    return result


def _build_pricing(raw: Dict[str, Any]) -> Dict[str, ModelPricing]:
    """从原始配置字典构建 model_pricing 映射"""
    pricing = {}
    for model_name, vals in raw.get("model_pricing", {}).items():
        if isinstance(vals, dict):
            pricing[model_name] = ModelPricing(**vals)
    return pricing


def load_config(config_path: str = "config.yaml") -> AppConfig:
    """加载 YAML 配置, 并用 .env 变量覆盖"""
    load_dotenv()

    # 解析 YAML
    yaml_path = Path(config_path)
    if yaml_path.exists():
        with open(yaml_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    else:
        raw = {}

    # 收集所有 YAML 字段到扁平字典
    flat = _flatten_yaml(raw)

    # 构建最终数据
    deepseek_keys: List[str] = []
    if "DEEPSEEK_API_KEYS" in os.environ:
        deepseek_keys = [k.strip() for k in os.environ["DEEPSEEK_API_KEYS"].split(",") if k.strip()]
    elif flat.get("deepseek_api_keys"):
        keys_str = flat["deepseek_api_keys"]
        deepseek_keys = [k.strip() for k in keys_str.split(",") if k.strip()]

    deepseek_base_url = os.environ.get("DEEPSEEK_BASE_URL") or flat.get("deepseek_base_url", "https://api.deepseek.com")

    # 构建配置
    data = {
        "server": {
            "host": os.environ.get("SERVER_HOST", flat.get("server_host", "0.0.0.0")),
            "port": int(os.environ.get("SERVER_PORT", flat.get("server_port", 8000))),
            "title": flat.get("server_title", "DeepSeek Proxy"),
            "description": flat.get("server_description", ""),
        },
        "deepseek": {
            "base_url": deepseek_base_url,
            "api_keys": deepseek_keys,
            "model_aliases": flat.get("deepseek_model_aliases", {}),
        },
        "cache": {
            "enabled": flat.get("cache_enabled", True),
            "db_path": os.environ.get("CACHE_DB_PATH", flat.get("cache_db_path", "./data/cache.db")),
            "ttl_hours": int(os.environ.get("CACHE_TTL_HOURS", flat.get("cache_ttl_hours", 24))),
            "max_entries": int(os.environ.get("CACHE_MAX_ENTRIES", flat.get("cache_max_entries", 10000))),
            "cache_stream": flat.get("cache_cache_stream", False),
        },
        "routing": {
            "default_model": flat.get("routing_default_model", "deepseek-v4-flash"),
            "force_model": flat.get("routing_force_model", ""),
            "content_rules": flat.get("routing_content_rules") or {},
        },
        "key_pool": {
            "strategy": flat.get("key_pool_strategy", "round_robin"),
            "backoff_seconds": int(flat.get("key_pool_backoff_seconds", 60)),
            "health_check_interval": int(flat.get("key_pool_health_check_interval", 300)),
        },
        "stats": {
            "db_path": os.environ.get("STATS_DB_PATH", flat.get("stats_db_path", "./data/stats.db")),
        },
    }

    # pricing 需要特殊处理
    pricing_raw = raw.get("pricing", {})
    data["pricing"] = {
        "model_pricing": _build_pricing(pricing_raw),
    }

    return AppConfig(**data)


# 全局单例
_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config(config_path: str = "config.yaml") -> AppConfig:
    global _config
    _config = load_config(config_path)
    return _config