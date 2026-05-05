"""
Encapsulado del cliente LLM
Llamadas unificadas en formato OpenAI
"""

import json
import os
import re
from typing import Optional, Dict, Any, List
from openai import OpenAI

from ..config import Config
from .logger import get_logger

logger = get_logger('mirofish.llm_client')


def _extract_json_object(text: str) -> Optional[str]:
    """Extrae el primer objeto JSON balanceado desde una respuesta textual."""
    start = text.find('{')
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text[start:], start=start):
        if escaped:
            escaped = False
            continue
        if char == '\\':
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


class LLMClient:
    """Cliente LLM"""

    # Modelos/proveedores que NO soportan response_format={"type":"json_object"}
    # Gemini via OpenAI-compatible API requiere omitir este parámetro
    _JSON_FORMAT_UNSUPPORTED_PATTERNS = [
        'generativelanguage.googleapis.com',
        'gemini',
    ]

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None
    ):
        self.api_key = api_key or Config.LLM_API_KEY
        self.base_url = base_url or Config.LLM_BASE_URL
        self.model = model or Config.LLM_MODEL_NAME

        if not self.api_key:
            raise ValueError("LLM_API_KEY no configurado")

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )

    def _supports_json_format(self) -> bool:
        """Determina si el modelo/proveedor soporta response_format json_object."""
        check_str = f"{self.base_url or ''} {self.model or ''}".lower()
        for pattern in self._JSON_FORMAT_UNSUPPORTED_PATTERNS:
            if pattern in check_str:
                return False
        return True

    def _default_reasoning_effort(self) -> Optional[str]:
        """Evitar que Gemini 2.5 Flash consuma el presupuesto de salida en thinking interno."""
        configured = os.environ.get("LLM_REASONING_EFFORT")
        if configured:
            return configured.strip().lower()

        check_str = f"{self.base_url or ''} {self.model or ''}".lower()
        if (
            "generativelanguage.googleapis.com" in check_str
            and "gemini-2.5" in check_str
            and "pro" not in check_str
        ):
            return "none"
        return None

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None
    ) -> str:
        """
        Enviar solicitud de chat

        Args:
            messages: lista de mensajes
            temperature: parámetro de temperatura
            max_tokens: número máximo de tokens
            response_format: formato de respuesta (ej. modo JSON)

        Returns:
            texto de respuesta del modelo
        """
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        reasoning_effort = self._default_reasoning_effort()
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort

        if response_format:
            kwargs["response_format"] = response_format

        logger.debug(f"Llamando LLM: model={self.model}, base_url={self.base_url}, temperature={temperature}, max_tokens={max_tokens}, response_format={response_format}, reasoning_effort={reasoning_effort}")

        try:
            response = self.client.chat.completions.create(**kwargs)
        except Exception as e:
            logger.error(f"Error en llamada LLM (model={self.model}, base_url={self.base_url}): {type(e).__name__}: {e}")
            raise

        content = response.choices[0].message.content
        logger.debug(f"Respuesta LLM recibida ({len(content or '')} chars): {(content or '')[:300]}")
        # Algunos modelos (ej. MiniMax M2.5) incluyen contenido <think> en el campo content, debe eliminarse
        content = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
        return content

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096
    ) -> Dict[str, Any]:
        """
        Enviar solicitud de chat y retornar JSON

        Args:
            messages: lista de mensajes
            temperature: parámetro de temperatura
            max_tokens: número máximo de tokens

        Returns:
            objeto JSON analizado
        """
        # Gemini y otros modelos de razonamiento no soportan response_format=json_object
        # En esos casos se confía en las instrucciones del system prompt para obtener JSON
        use_json_format = self._supports_json_format()
        if not use_json_format:
            logger.info(f"Modelo '{self.model}' no soporta response_format json_object — omitiendo parámetro, confiando en system prompt")

        response = self.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"} if use_json_format else None
        )
        # Limpiar marcadores de bloque de código markdown
        cleaned_response = response.strip()
        cleaned_response = re.sub(r'^```(?:json)?\s*\n?', '', cleaned_response, flags=re.IGNORECASE)
        cleaned_response = re.sub(r'\n?```\s*$', '', cleaned_response)
        cleaned_response = cleaned_response.strip()

        logger.debug(f"JSON limpiado para parsear ({len(cleaned_response)} chars): {cleaned_response[:200]}")

        try:
            return json.loads(cleaned_response)
        except json.JSONDecodeError as e:
            extracted_json = _extract_json_object(cleaned_response)
            if extracted_json and extracted_json != cleaned_response:
                try:
                    return json.loads(extracted_json)
                except json.JSONDecodeError:
                    pass
            logger.error(f"Error al parsear JSON del LLM: {e}")
            logger.error(f"Respuesta completa del LLM: {cleaned_response[:2000]}")
            raise ValueError(f"Formato JSON inválido retornado por LLM: {cleaned_response}")
