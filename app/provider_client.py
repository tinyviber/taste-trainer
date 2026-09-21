"""Signed client for using one user's encrypted provider from Python jobs."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4


class ProviderClient:
    def __init__(self, service_url: str, internal_secret: str, user_id: str, provider_id: str):
        self._service_url = service_url.rstrip("/")
        self._secret = internal_secret.encode("utf-8")
        self._user_id = user_id
        self._provider_id = provider_id
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, *, model: str, messages: list[dict], temperature: float = 0.7, **kwargs):
        payload = {"model": model, "messages": messages, "temperature": temperature, **kwargs}
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        path = f"/providers/{quote(self._provider_id, safe='')}/completions"
        timestamp = str(int(time.time()))
        nonce = uuid4().hex
        canonical = "\n".join((timestamp, nonce, "POST", path,
                                hashlib.sha256(body).hexdigest(), self._user_id))
        signature = hmac.new(self._secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        request = Request(
            self._service_url + path,
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Provider-User": self._user_id,
                "X-Provider-Timestamp": timestamp,
                "X-Provider-Nonce": nonce,
                "X-Provider-Signature": signature,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=120) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise RuntimeError(f"default provider request failed ({exc.code}): {detail}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("default provider service unavailable") from exc
        try:
            message = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("default provider returned an invalid chat response") from exc
        if isinstance(message, list):
            message = "".join(
                part.get("text", "") for part in message
                if isinstance(part, dict) and isinstance(part.get("text", ""), str)
            )
        if not isinstance(message, str):
            raise RuntimeError("default provider returned an invalid chat message")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=message))]
        )
