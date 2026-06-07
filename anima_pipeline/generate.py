"""Gemma 4 を動かすローカル llama-server 用のチャット + 翻訳クライアント。

OpenAI 互換の /v1/chat/completions エンドポイントを使う。`response_format` に
よる JSON スキーマ制約出力と、(任意で)生の GBNF `grammar` をサポートする。
"""
from __future__ import annotations
import requests

import config


class ChatClient:
    def __init__(self, base_url: str = config.CHAT_URL, timeout: int = 300):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def chat(self, messages: list[dict], response_format: dict | None = None,
             grammar: str | None = None,
             temperature: float = config.GEN_TEMPERATURE,
             max_tokens: int = config.GEN_MAX_TOKENS,
             top_p: float = 0.95, top_k: int = 64) -> str:
        payload: dict = {
            "messages": messages,
            "temperature": temperature,
            "top_p": top_p,
            "top_k": top_k,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        if grammar is not None:
            payload["grammar"] = grammar          # llama-server の拡張
        r = requests.post(f"{self.base}/v1/chat/completions",
                          json=payload, timeout=self.timeout)
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        # 新しめの llama.cpp は、出力を reasoning_content 側に入れて content を
        # 空にすることがある(gemma4 テンプレート互換時など)。空なら拾い直す。
        content = (msg.get("content") or "").strip()
        if not content:
            content = (msg.get("reasoning_content") or "").strip()
        return content

    def translate_ja_en(self, ja_text: str, temperature: float = 0.1) -> str:
        messages = [
            {"role": "system", "content":
                "You are a translator. Translate the user's Japanese image "
                "description into natural English. Output ONLY the English "
                "translation: no notes, no quotes, no explanation."},
            {"role": "user", "content": ja_text},
        ]
        return self.chat(messages, temperature=temperature, max_tokens=300).strip()
