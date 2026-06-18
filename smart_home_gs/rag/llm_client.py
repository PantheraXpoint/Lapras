"""OpenAI-compatible client for a hosted vLLM chat model.

This replaces the previous in-process dependency on the sibling INDUS repo
(`llms.init_model` / lmdeploy). Instead of loading a model into this process,
we talk HTTP to a vLLM server started with e.g.:

    vllm serve Qwen/Qwen3-8B --port 8000

which exposes an OpenAI-compatible `/v1/chat/completions` endpoint.

Configuration is read from the environment (with sane defaults) so the same
code runs against any host/port without edits:

    LLM_BASE_URL   default "http://localhost:8000/v1"
    LLM_MODEL      default "Qwen/Qwen3-8B"
    OPENAI_API_KEY default "dummy"  (vLLM ignores it unless --api-key is set)
"""

import os
from typing import Any, Dict

from openai import OpenAI

DEFAULT_BASE_URL = "http://localhost:8000/v1"
DEFAULT_MODEL = "Qwen/Qwen3-8B"


class LLMClient:
    """Thin wrapper exposing the same `generate_response` interface the caption
    scripts used to get from INDUS's QwenLM, but backed by a hosted vLLM server.
    """

    def __init__(self, base_url: str = None, model: str = None, api_key: str = None):
        self.base_url = base_url or os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL)
        self.model = model or os.getenv("LLM_MODEL", DEFAULT_MODEL)
        self.client = OpenAI(
            api_key=api_key or os.getenv("OPENAI_API_KEY", "dummy"),
            base_url=self.base_url,
        )

    def generate_response(
        self, inputs: Dict[str, Any], max_new_tokens: int = 512, temperature: float = 0.5
    ) -> str:
        """Generate a text response.

        Args:
            inputs: dict containing a "text" prompt, e.g. {"text": "..."}.
            max_new_tokens: maximum tokens to generate.
            temperature: sampling temperature.

        Returns:
            The generated text.
        """
        assert "text" in inputs, "Please provide a text prompt under 'text'."
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": inputs["text"]}],
            max_tokens=max_new_tokens,
            temperature=temperature,
        )
        return response.choices[0].message.content


def init_llm_client(model: str = None, base_url: str = None) -> LLMClient:
    """Drop-in replacement for the old `init_model(...)` factory."""
    return LLMClient(base_url=base_url, model=model)
