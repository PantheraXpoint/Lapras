"""OpenAI-compatible client for a hosted vLLM chat model.

Rather than loading a model into this process, we talk HTTP to a vLLM server
started with e.g.:

    vllm serve Qwen/Qwen3-8B --port 8000

which exposes an OpenAI-compatible `/v1/chat/completions` endpoint.

Configuration is read from the environment (with sane defaults) so the same
code runs against any host/port without edits:

    LLM_BASE_URL        default "http://localhost:8000/v1"
    LLM_MODEL           default "Qwen/Qwen3-8B"
    OPENAI_API_KEY      default "dummy"  (vLLM ignores it unless --api-key is set)
    LLM_TIMEOUT         default "60"     per-request timeout in seconds
    LLM_ENABLE_THINKING default "false"  set "true" to keep Qwen3's <think> block

Qwen3 is a reasoning model that, by default, writes a long internal <think>
block before its answer. That makes each request slow. We disable it per
request (no server restart needed) unless LLM_ENABLE_THINKING=true.
"""

import os
from typing import Any, Dict

from openai import OpenAI

DEFAULT_BASE_URL = "http://localhost:8000/v1"
DEFAULT_MODEL = "Qwen/Qwen3-8B"
DEFAULT_TIMEOUT = 60.0


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


class LLMClient:
    """Thin wrapper exposing a `generate_response` interface backed by a hosted
    vLLM server.
    """

    def __init__(
        self,
        base_url: str = None,
        model: str = None,
        api_key: str = None,
        timeout: float = None,
        enable_thinking: bool = None,
    ):
        self.base_url = base_url or os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL)
        self.model = model or os.getenv("LLM_MODEL", DEFAULT_MODEL)
        self.timeout = timeout if timeout is not None else float(os.getenv("LLM_TIMEOUT", DEFAULT_TIMEOUT))
        self.enable_thinking = (
            enable_thinking if enable_thinking is not None else _env_bool("LLM_ENABLE_THINKING", False)
        )
        self.client = OpenAI(
            api_key=api_key or os.getenv("OPENAI_API_KEY", "dummy"),
            base_url=self.base_url,
            timeout=self.timeout,
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
        # Qwen3 emits a long <think> block by default; disable it per request
        # (via vLLM's chat_template_kwargs) to keep captions fast unless the
        # caller explicitly wants thinking enabled.
        extra_body = None
        if not self.enable_thinking:
            extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": inputs["text"]}],
            max_tokens=max_new_tokens,
            temperature=temperature,
            extra_body=extra_body,
        )
        return response.choices[0].message.content


def init_llm_client(
    model: str = None,
    base_url: str = None,
    timeout: float = None,
    enable_thinking: bool = None,
) -> LLMClient:
    """Construct an LLMClient for the configured (or overridden) vLLM endpoint."""
    return LLMClient(
        base_url=base_url, model=model, timeout=timeout, enable_thinking=enable_thinking
    )
