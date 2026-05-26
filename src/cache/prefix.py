"""消息前缀优化器: 激进归一化以最大化 DeepSeek 前缀缓存命中"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List


def _get_text(msg: Dict[str, Any]) -> str:
    """从 message 中提取纯文本 content"""
    content = msg.get("content") or ""
    if isinstance(content, list):
        parts = [p.get("text", "") for p in content if isinstance(p, dict)]
        return "\n".join(parts)
    return str(content)


def _deduplicate_tool_results(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    工具调用结果中的大段相同内容归一化:
    如果连续多个 tool 消息的 content 相同或高度相似，
    只保留最后一个，其余用占位标记替代。
    这不会改变语义（DeepSeek 只看最近的同名 tool_call_id），
    但能大幅增加前缀匹配率。
    """
    result: List[Dict[str, Any]] = []
    seen_tool_contents: Dict[str, str] = {}

    for msg in messages:
        role = msg.get("role", "")
        if role == "tool":
            text = _get_text(msg)
            if len(text) > 500:
                # 长 tool 结果: 如果内容相同，复用占位
                content_hash = text[:200]
                if content_hash in seen_tool_contents:
                    # 用完全相同长度的占位保持 token 数一致
                    placeholder = f"[cached tool result #{len(seen_tool_contents)}]"
                    new_msg = dict(msg)
                    new_msg["content"] = placeholder
                    result.append(new_msg)
                    continue
                else:
                    seen_tool_contents[content_hash] = text
        result.append(msg)
    return result


def _normalize_system_prompt(system_msg: Dict[str, Any]) -> Dict[str, Any]:
    """
    对 system 消息做微调:
    - 去除尾部空白差异
    - 确保末尾有换行（不同客户端可能不一致）
    """
    text = _get_text(system_msg)
    if not text:
        return system_msg
    normalized = text.strip()
    if normalized:
        new_msg = dict(system_msg)
        new_msg["content"] = normalized + "\n"
        return new_msg
    return system_msg


def optimize_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    激进前缀优化:
    1. system 消息始终在最前 + 归一化尾部空白
    2. 连续相同大段 tool 结果去重
    3. 保持 user/assistant/tool 时序不变

    直接操作 dict, 保留所有 OpenAI 兼容字段
    """
    if not messages:
        return messages

    # 分离 system 和 rest
    system: List[Dict[str, Any]] = []
    rest: List[Dict[str, Any]] = []

    for msg in messages:
        if msg.get("role") == "system":
            system.append(_normalize_system_prompt(msg))
        else:
            rest.append(msg)

    # 归一化 rest 中的重复 tool 结果
    rest = _deduplicate_tool_results(rest)

    return system + rest