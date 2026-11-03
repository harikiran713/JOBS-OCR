"""Local inference provider abstraction.

Supported runtimes:
* **Ollama** (``/api/chat``, ``/api/embed``, ``/api/tags``, ``/api/pull``)
* **OpenAI-compatible servers** – llama.cpp ``llama-server``, LM Studio,
  vLLM, etc. (``/v1/chat/completions``, ``/v1/embeddings``, ``/v1/models``)

No model is hard-coded: chat, embedding and vision model names come from
settings. Requests go only to loopback addresses unless the user explicitly
enables ``ai.allow_remote`` — document content never leaves the machine by
default.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import logging
import socket
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)


class AIUnavailable(RuntimeError):
    pass


class AIResponseError(RuntimeError):
    pass


@dataclass
class AIConfig:
    provider: str
    base_url: str
    chat_model: str
    embedding_model: str
    vision_model: str
    allow_remote: bool
    timeout_s: float

    @classmethod
    def from_settings(cls, s: dict[str, Any]) -> AIConfig:
        return cls(
            provider=s.get("ai.provider", "none"),
            base_url=(s.get("ai.base_url") or "").rstrip("/"),
            chat_model=s.get("ai.chat_model", ""),
            embedding_model=s.get("ai.embedding_model", ""),
            vision_model=s.get("ai.vision_model", ""),
            allow_remote=bool(s.get("ai.allow_remote", False)),
            timeout_s=float(s.get("ai.timeout_s", 240)),
        )


def is_loopback_url(url: str) -> bool:
    host = urlparse(url).hostname or ""
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        try:
            return all(ipaddress.ip_address(info[4][0]).is_loopback for info in socket.getaddrinfo(host, None))
        except OSError:
            return False


class Provider(Protocol):
    config: AIConfig

    def chat_json(self, system: str, user: str, schema: dict, model: str | None = None) -> dict: ...
    def chat_text(self, system: str, user: str, model: str | None = None) -> str: ...
    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def vision_text(self, png: bytes, prompt: str) -> str: ...
    def list_models(self) -> list[dict]: ...


class _Base:
    def __init__(self, config: AIConfig):
        if not config.base_url:
            raise AIUnavailable("No inference server URL configured")
        if not config.allow_remote and not is_loopback_url(config.base_url):
            raise AIUnavailable(
                "The configured AI endpoint is not on this computer. Enable 'Allow non-local AI endpoints' "
                "in Settings to use it (document text would be sent to that server)."
            )
        self.config = config
        self._client = httpx.Client(base_url=config.base_url, timeout=httpx.Timeout(config.timeout_s, connect=5.0))

    def _post(self, path: str, payload: dict) -> dict:
        try:
            r = self._client.post(path, json=payload)
        except httpx.ConnectError as exc:
            raise AIUnavailable(f"Cannot reach inference server at {self.config.base_url}") from exc
        except httpx.TimeoutException as exc:
            raise AIResponseError(f"Inference request timed out after {self.config.timeout_s:.0f}s") from exc
        if r.status_code >= 400:
            raise AIResponseError(f"Inference server error {r.status_code}: {r.text[:300]}")
        return r.json()

    def _get(self, path: str) -> dict:
        try:
            r = self._client.get(path)
        except httpx.ConnectError as exc:
            raise AIUnavailable(f"Cannot reach inference server at {self.config.base_url}") from exc
        if r.status_code >= 400:
            raise AIResponseError(f"Inference server error {r.status_code}: {r.text[:300]}")
        return r.json()

    @staticmethod
    def _parse_json(text: str) -> dict:
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{") :]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    pass
        raise AIResponseError("Model did not return valid JSON")


class OllamaProvider(_Base):
    name = "ollama"

    def chat_json(self, system: str, user: str, schema: dict, model: str | None = None) -> dict:
        model = model or self.config.chat_model
        if not model:
            raise AIUnavailable("No chat model configured")
        data = self._post(
            "/api/chat",
            {
                "model": model,
                "stream": False,
                "format": schema,
                "options": {"temperature": 0},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
        )
        return self._parse_json(data.get("message", {}).get("content", ""))

    def chat_text(self, system: str, user: str, model: str | None = None) -> str:
        model = model or self.config.chat_model
        if not model:
            raise AIUnavailable("No chat model configured")
        data = self._post(
            "/api/chat",
            {
                "model": model,
                "stream": False,
                "options": {"temperature": 0},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
        )
        return data.get("message", {}).get("content", "")

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.config.embedding_model:
            raise AIUnavailable("No embedding model configured")
        data = self._post("/api/embed", {"model": self.config.embedding_model, "input": texts})
        return data.get("embeddings", [])

    def vision_text(self, png: bytes, prompt: str) -> str:
        if not self.config.vision_model:
            raise AIUnavailable("No vision model configured")
        data = self._post(
            "/api/chat",
            {
                "model": self.config.vision_model,
                "stream": False,
                "options": {"temperature": 0},
                "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(png).decode()]}],
            },
        )
        return data.get("message", {}).get("content", "")

    def list_models(self) -> list[dict]:
        data = self._get("/api/tags")
        return [
            {"name": m.get("name"), "size_bytes": m.get("size"), "details": m.get("details", {})}
            for m in data.get("models", [])
        ]

    def pull(self, model: str):  # noqa: ANN201 - generator of progress dicts
        """Stream download progress for an explicit, user-initiated model download."""
        with self._client.stream("POST", "/api/pull", json={"model": model, "stream": True}, timeout=None) as r:
            if r.status_code >= 400:
                raise AIResponseError(f"Pull failed: {r.read()[:300]!r}")
            for line in r.iter_lines():
                if line.strip():
                    yield json.loads(line)


class OpenAICompatProvider(_Base):
    name = "openai_compatible"

    def _chat(self, model: str, messages: list[dict], schema: dict | None) -> str:
        payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0}
        if schema is not None:
            payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "schema": schema}}
        data = self._post("/v1/chat/completions", payload)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise AIResponseError("Unexpected response from inference server") from exc

    def chat_json(self, system: str, user: str, schema: dict, model: str | None = None) -> dict:
        model = model or self.config.chat_model or "default"
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        try:
            return self._parse_json(self._chat(model, msgs, schema))
        except AIResponseError:
            # Some servers reject json_schema; retry with instructions only.
            msgs[0]["content"] += "\nRespond with JSON only, matching this schema: " + json.dumps(schema)
            return self._parse_json(self._chat(model, msgs, None))

    def chat_text(self, system: str, user: str, model: str | None = None) -> str:
        model = model or self.config.chat_model or "default"
        return self._chat(model, [{"role": "system", "content": system}, {"role": "user", "content": user}], None)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.config.embedding_model:
            raise AIUnavailable("No embedding model configured")
        data = self._post("/v1/embeddings", {"model": self.config.embedding_model, "input": texts})
        return [d["embedding"] for d in sorted(data.get("data", []), key=lambda d: d.get("index", 0))]

    def vision_text(self, png: bytes, prompt: str) -> str:
        if not self.config.vision_model:
            raise AIUnavailable("No vision model configured")
        url = "data:image/png;base64," + base64.b64encode(png).decode()
        msgs = [{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": url}}]}]
        return self._chat(self.config.vision_model, msgs, None)

    def list_models(self) -> list[dict]:
        data = self._get("/v1/models")
        return [{"name": m.get("id"), "size_bytes": None, "details": {}} for m in data.get("data", [])]


def get_provider(settings: dict[str, Any]) -> OllamaProvider | OpenAICompatProvider | None:
    """Return a configured provider or ``None`` when AI is disabled. Raises AIUnavailable on bad config."""
    cfg = AIConfig.from_settings(settings)
    if cfg.provider == "ollama":
        return OllamaProvider(cfg)
    if cfg.provider == "openai_compatible":
        return OpenAICompatProvider(cfg)
    return None


def try_provider(settings: dict[str, Any]) -> OllamaProvider | OpenAICompatProvider | None:
    try:
        return get_provider(settings)
    except AIUnavailable as exc:
        log.warning("AI provider unavailable: %s", exc)
        return None
