"""OpenAI-compatible LLM client helpers.

All jobs go through chat_json(): the model is instructed to return a single
JSON object; we extract and parse it robustly (fenced blocks, stray prose).
"""
import base64
import json
import re

from openai import OpenAI


def make_client(base_url: str, api_key: str) -> OpenAI:
    return OpenAI(base_url=base_url, api_key=api_key)


def image_part(path) -> dict:
    """OpenAI-compatible vision message part (data URI)."""
    b64 = base64.b64encode(path.read_bytes()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


def text_part(text: str) -> dict:
    return {"type": "text", "text": text}


def _extract_json(raw: str) -> dict:
    raw = raw.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.S)
    if m:
        raw = m.group(1)
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    return json.loads(raw[start:end + 1])


def chat_json(client: OpenAI, model: str, system: str, user, images: list | None = None,
              retries: int = 2, temperature: float = 0.7) -> dict:
    """One call that must return a JSON object. Retries with an error hint."""
    if isinstance(user, str):
        user = [text_part(user)]
    if images:
        user = user + [image_part(p) for p in images]
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    last_err = None
    for _ in range(retries + 1):
        resp = client.chat.completions.create(model=model, messages=messages,
                                              temperature=temperature)
        raw = resp.choices[0].message.content or ""
        try:
            return _extract_json(raw)
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content":
                             "输出无法解析为 JSON。请只输出一个 JSON 对象，不要包含其他文字。"})
    raise RuntimeError(f"LLM did not return valid JSON after retries: {last_err}")
