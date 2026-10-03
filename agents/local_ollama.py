"""Native Ollama JSON calls with explicit settings and an auditable retry bound."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from typing import Any, Callable

import httpx

JSON_RETRY_PROMPT = (
    "上一条请求未通过响应校验。请重新回答，只返回完整、合法的 JSON 对象，"
    "严格遵守原提示词的字段、类型与数据证据要求，不要输出标题式正文或额外解释。"
)


class LocalOllamaClient:
    """Use one configured endpoint; no cloud fallback or automatic model pulls."""

    def __init__(
        self, host: str, *, timeout: float = 180, attempts: int = 3,
        num_ctx: int = 16384, transport: httpx.AsyncBaseTransport | None = None,
        audit: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.attempts = attempts
        self.num_ctx = num_ctx
        self.transport = transport
        self.audit = audit or (lambda item: None)

    async def generate_json(
        self, *, stage: str, model: str, messages: list[dict[str, Any]],
        validate: Callable[[Any], dict[str, Any]], schema: dict[str, Any] | None = None,
        num_predict: int = 1800,
    ) -> dict[str, Any]:
        """Retry transport, incomplete generation, JSON, and schema failures."""
        payload = {
            "model": model, "messages": messages, "stream": False,
            "format": schema or "json", "think": False,
            "options": {"temperature": 0, "seed": 42,
                        "num_ctx": self.num_ctx, "num_predict": num_predict},
        }
        # Save reproducible text, with hashes instead of large base64 images.
        audit_messages = [
            {**{k: v for k, v in m.items() if k != "images"},
             **({"image_base64_sha256": [
                 hashlib.sha256(v.encode()).hexdigest() for v in m["images"]
             ]} if "images" in m else {})}
            for m in messages
        ]
        async with httpx.AsyncClient(
            trust_env=False, timeout=self.timeout, transport=self.transport,
        ) as client:
            for attempt in range(1, self.attempts + 1):
                started = time.perf_counter()
                record = {"stage": stage, "attempt": attempt, "host": self.host,
                          "request": {**payload, "messages": audit_messages}}
                try:
                    response = await client.post(f"{self.host}/api/chat", json=payload)
                    if response.is_error:
                        record["http_status"] = response.status_code
                        record["server_error"] = response.text[:2000]
                    response.raise_for_status()
                    data = response.json()
                    record["response"] = data
                    if not isinstance(data, dict) or data.get("done") is not True:
                        raise ValueError("Ollama returned an incomplete response")
                    if data.get("done_reason") == "length":
                        raise ValueError("Ollama output reached num_predict; increase the token limit")
                    message = data.get("message")
                    content = message.get("content") if isinstance(message, dict) else None
                    if not isinstance(content, str) or not content.strip():
                        raise ValueError("Ollama returned empty message.content")
                    # Some local runners wrap valid JSON despite format="json".
                    # Only unwrap a complete fence; never salvage partial objects.
                    fenced = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", content.strip(), re.DOTALL)
                    result = validate(json.loads(fenced.group(1) if fenced else content))
                except (httpx.HTTPError, ValueError) as exc:
                    record.update(status="error", error=f"{type(exc).__name__}: {exc}")
                    correction = JSON_RETRY_PROMPT + "\n校验失败原因：" + str(exc)[:1000]
                    if attempt == self.attempts:
                        raise RuntimeError(
                            f"{stage} failed after {attempt} attempts: {exc}. "
                            f"Check Ollama at {self.host} and installed model {model}."
                        ) from exc
                else:
                    record["status"] = "accepted"
                    return result
                finally:
                    record["elapsed_seconds"] = round(time.perf_counter() - started, 3)
                    self.audit(record)
                payload = {**payload, "messages": [
                    *messages, {"role": "user", "content": correction},
                ]}
                audit_messages = [*audit_messages[:len(messages)],
                                  {"role": "user", "content": correction}]
                await asyncio.sleep(min(attempt, 2))
        raise RuntimeError("Ollama attempts must be positive")
