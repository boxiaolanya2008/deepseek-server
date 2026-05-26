"""智能模型路由: 别名映射 + 基于内容的 Flash/Pro 自动选择"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple

from src.config import get_config

log = logging.getLogger("router.model")


class ModelRouter:
    def __init__(
        self,
        aliases: Dict[str, str],
        content_rules: Dict[str, str],
        default_model: str,
        force_model: str,
    ):
        self._aliases = aliases
        self._content_rules = content_rules
        self._default = default_model
        self._force = force_model

        # 将内容规则编译为正则
        self._rule_patterns: List[Tuple[re.Pattern, str]] = []
        for keyword, model in content_rules.items():
            self._rule_patterns.append((re.compile(re.escape(keyword)), model))

    def resolve_alias(self, model: str) -> str:
        """将客户端请求的 model 映射为实际 model"""
        return self._aliases.get(model, model)

    def route_by_content(
        self,
        messages: List[Dict],
        requested_model: str,
    ) -> str:
        """
        基于对话内容选择模型:
        - 如果强制指定了模型, 直接返回
        - 否则按内容规则匹配, 匹配到则使用对应模型
        - 最终结果再做一次别名映射
        """
        if self._force:
            resolved = self._force
            log.debug(f"强制模型: {resolved}")
            return resolved

        # 从 messages 中提取纯文本内容用于匹配
        content_text = ""
        for msg in messages:
            if isinstance(msg, dict) and "content" in msg:
                content_text += " " + str(msg["content"])

        content_text_lower = content_text.lower()

        for pattern, model in self._rule_patterns:
            if pattern.search(content_text_lower):
                log.debug(f"内容匹配 '{pattern.pattern}', 路由到 {model}")
                return model

        # 默认使用请求中指定的模型 (经别名解析后)
        log.debug(f"无匹配规则, 使用默认模型: {requested_model}")
        return requested_model

    def resolve(
        self,
        model: str,
        messages: List[Dict],
    ) -> str:
        """综合入口: 先别名映射, 再内容路由, 最后返回实际 model"""
        aliased = self.resolve_alias(model)
        routed = self.route_by_content(messages, aliased)
        return routed


# 全局单例
_router: Optional[ModelRouter] = None


def get_model_router() -> ModelRouter:
    global _router
    if _router is None:
        cfg = get_config()
        _router = ModelRouter(
            aliases=cfg.deepseek.model_aliases,
            content_rules=cfg.routing.content_rules,
            default_model=cfg.routing.default_model,
            force_model=cfg.routing.force_model,
        )
    return _router


def reset_model_router():
    """重置模型路由器, 下次 get_model_router() 时从新配置重新创建"""
    global _router
    _router = None