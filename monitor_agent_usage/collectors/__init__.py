"""Collector registry for Grok, Codex, Antigravity, and Claude."""

from .antigravity import AntigravityCollector
from .base import BaseCollector, extract_clean_session_title, make_event_id, normalize_interface, parse_timestamp
from .claude import ClaudeCollector
from .codex import CodexCollector
from .grok import GrokCollector

__all__ = [
    "AntigravityCollector",
    "BaseCollector",
    "ClaudeCollector",
    "CodexCollector",
    "GrokCollector",
    "extract_clean_session_title",
    "make_event_id",
    "normalize_interface",
    "parse_timestamp",
]
