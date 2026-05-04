"""
Runtime safety limits for OASIS/CAMEL simulations.

OASIS creates SocialAgent instances internally, so the application cannot pass
CAMEL memory options directly from the simulation runner. This module patches
CAMEL once, before agent graphs are generated, to keep long simulations from
building unbounded per-agent chat history.
"""

from __future__ import annotations

import os
from typing import Any, Optional


DEFAULT_MESSAGE_WINDOW_SIZE = 8
DEFAULT_MEMORY_RECORDS = 24
DEFAULT_TOKEN_LIMIT = 64000
DEFAULT_STEP_TIMEOUT_SECONDS = 180.0


def _uses_gemini_openai_compat() -> bool:
    model_names = " ".join(
        filter(
            None,
            [
                os.environ.get("LLM_MODEL_NAME", ""),
                os.environ.get("LLM_BOOST_MODEL_NAME", ""),
            ],
        )
    ).lower()
    base_urls = " ".join(
        filter(
            None,
            [
                os.environ.get("LLM_BASE_URL", ""),
                os.environ.get("LLM_BOOST_BASE_URL", ""),
                os.environ.get("OPENAI_API_BASE_URL", ""),
                os.environ.get("OPENAI_BASE_URL", ""),
            ],
        )
    ).lower()
    return "gemini" in model_names or "generativelanguage.googleapis.com" in base_urls


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
        return max(minimum, value)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float, minimum: float = 1.0) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
        return max(minimum, value)
    except (TypeError, ValueError):
        return default


def _memory_storage(agent: Any) -> Optional[Any]:
    memory = getattr(agent, "memory", None)
    block = getattr(memory, "_chat_history_block", None)
    return getattr(block, "storage", None)


def _is_tool_related_record(record: Any) -> bool:
    if not isinstance(record, dict):
        return False

    role = record.get("role_at_backend")
    if role in {"function", "tool"}:
        return True

    meta_dict = record.get("meta_dict")
    message = record.get("message")
    if isinstance(message, dict):
        meta_dict = meta_dict or message.get("meta_dict")

    if role == "assistant":
        if isinstance(meta_dict, dict) and "tool_calls" in meta_dict:
            return True
        if isinstance(message, dict) and (
            message.get("func_name") or message.get("tool_call_id")
        ):
            return True

    return False


def _save_records(storage: Any, records: list[Any]) -> None:
    storage.clear()
    storage.save(records)


def trim_agent_memory(agent: Any, max_records: Optional[int] = None) -> None:
    """
    Keep the system/developer message plus the most recent records.

    CAMEL's ChatHistoryMemory can retrieve a window, but the backing storage can
    still grow indefinitely. Trimming the storage prevents late rounds from
    spending CPU copying and scoring hundreds of thousands of tokens.
    """
    storage = _memory_storage(agent)
    if storage is None:
        return

    max_records = max_records or _env_int(
        "MIROFISH_AGENT_MEMORY_RECORDS",
        DEFAULT_MEMORY_RECORDS,
    )

    try:
        records = storage.load()
    except Exception:
        return

    if not isinstance(records, list):
        return

    compacted_records = [
        record for record in records if not _is_tool_related_record(record)
    ]
    if len(compacted_records) != len(records):
        try:
            _save_records(storage, compacted_records)
        except Exception:
            return
        records = compacted_records

    if len(records) <= max_records:
        return

    first_role = records[0].get("role_at_backend") if isinstance(records[0], dict) else None
    preserve_first = first_role in {"system", "developer"}
    preserved = records[:1] if preserve_first else []
    sliding = records[1:] if preserve_first else records
    trimmed = preserved + sliding[-max_records:]

    try:
        _save_records(storage, trimmed)
    except Exception:
        return


def _patch_function_tool_response_names() -> None:
    from camel.messages import FunctionCallingMessage

    if getattr(FunctionCallingMessage, "_mirofish_tool_name_patched", False):
        return

    original_to_openai_tool_message = FunctionCallingMessage.to_openai_tool_message

    def to_openai_tool_message_with_name(self):
        message = original_to_openai_tool_message(self)
        if (
            _uses_gemini_openai_compat()
            and self.func_name
            and isinstance(message, dict)
        ):
            message.setdefault("name", self.func_name)
        return message

    FunctionCallingMessage.to_openai_tool_message = to_openai_tool_message_with_name
    FunctionCallingMessage._mirofish_tool_name_patched = True


def _patch_memory_tool_cleanup() -> None:
    from camel.memories.agent_memories import ChatHistoryMemory

    if getattr(ChatHistoryMemory, "_mirofish_tool_cleanup_patched", False):
        return

    def clean_tool_calls(self) -> None:
        block = getattr(self, "_chat_history_block", None)
        storage = getattr(block, "storage", None)
        if storage is None:
            return

        try:
            records = storage.load()
        except Exception:
            return

        if not isinstance(records, list):
            return

        cleaned = [
            record for record in records if not _is_tool_related_record(record)
        ]
        if len(cleaned) == len(records):
            return

        try:
            _save_records(storage, cleaned)
        except Exception:
            return

    ChatHistoryMemory.clean_tool_calls = clean_tool_calls
    ChatHistoryMemory._mirofish_tool_cleanup_patched = True


def _ensure_gemini_tool_response_names(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not _uses_gemini_openai_compat():
        return messages

    tool_names_by_id: dict[str, str] = {}
    sanitized: list[dict[str, Any]] = []

    for message in messages:
        next_message = dict(message)
        if next_message.get("role") == "assistant":
            tool_calls = next_message.get("tool_calls") or []
            if isinstance(tool_calls, list):
                for tool_call in tool_calls:
                    if not isinstance(tool_call, dict):
                        continue
                    tool_call_id = tool_call.get("id")
                    function = tool_call.get("function") or {}
                    function_name = function.get("name") if isinstance(function, dict) else None
                    if tool_call_id and function_name:
                        tool_names_by_id[str(tool_call_id)] = str(function_name)

        if next_message.get("role") == "tool":
            tool_call_id = next_message.get("tool_call_id")
            tool_name = next_message.get("name") or tool_names_by_id.get(str(tool_call_id))
            next_message["name"] = tool_name or "tool_response"

        sanitized.append(next_message)

    return sanitized


def _patch_model_message_sanitizer() -> None:
    from camel.models.base_model import BaseModelBackend

    if getattr(BaseModelBackend, "_mirofish_message_sanitizer_patched", False):
        return

    original_preprocess_messages = BaseModelBackend.preprocess_messages

    def preprocess_messages_with_tool_names(self, messages):
        processed = original_preprocess_messages(self, messages)
        if isinstance(processed, list):
            return _ensure_gemini_tool_response_names(processed)
        return processed

    BaseModelBackend.preprocess_messages = preprocess_messages_with_tool_names
    BaseModelBackend._mirofish_message_sanitizer_patched = True


def configure_camel_runtime_limits() -> None:
    """
    Patch CAMEL ChatAgent defaults used by OASIS SocialAgent.

    Environment overrides:
    - MIROFISH_AGENT_MESSAGE_WINDOW_SIZE
    - MIROFISH_AGENT_MEMORY_RECORDS
    - MIROFISH_AGENT_TOKEN_LIMIT
    - MIROFISH_AGENT_STEP_TIMEOUT_SECONDS
    """
    from camel.agents import ChatAgent

    _patch_function_tool_response_names()
    _patch_memory_tool_cleanup()
    _patch_model_message_sanitizer()

    if getattr(ChatAgent, "_mirofish_runtime_limits_patched", False):
        return

    original_init = ChatAgent.__init__
    original_record_message = ChatAgent.record_message

    def limited_init(self, *args, **kwargs):
        kwargs.setdefault(
            "message_window_size",
            _env_int(
                "MIROFISH_AGENT_MESSAGE_WINDOW_SIZE",
                DEFAULT_MESSAGE_WINDOW_SIZE,
            ),
        )
        kwargs.setdefault(
            "token_limit",
            _env_int("MIROFISH_AGENT_TOKEN_LIMIT", DEFAULT_TOKEN_LIMIT),
        )
        kwargs.setdefault(
            "step_timeout",
            _env_float(
                "MIROFISH_AGENT_STEP_TIMEOUT_SECONDS",
                DEFAULT_STEP_TIMEOUT_SECONDS,
            ),
        )
        kwargs.setdefault("prune_tool_calls_from_memory", True)
        original_init(self, *args, **kwargs)

    def limited_record_message(self, message, role, timestamp=None):
        result = original_record_message(self, message, role, timestamp)
        trim_agent_memory(self)
        return result

    ChatAgent.__init__ = limited_init
    ChatAgent.record_message = limited_record_message
    ChatAgent._mirofish_runtime_limits_patched = True
