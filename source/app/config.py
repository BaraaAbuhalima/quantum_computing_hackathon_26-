from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class OpenAIConfig:
    api_key: str
    model: str
    temperature: float
    max_tokens: int
    base_url: str | None = None

    @staticmethod
    def from_env() -> "OpenAIConfig":
        # Prefer DeepSeek if configured, otherwise fall back to OpenAI
        ds_key = os.environ.get("DEEPSEEK_API_KEY")
        if ds_key:
            model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
            temperature = float(os.environ.get("DEEPSEEK_TEMPERATURE", os.environ.get("OPENAI_TEMPERATURE", "0.2")))
            max_tokens = int(os.environ.get("DEEPSEEK_MAX_TOKENS", os.environ.get("OPENAI_MAX_TOKENS", "3000")))
            return OpenAIConfig(
                api_key=ds_key,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                base_url="https://api.deepseek.com",
            )

        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "Missing API key. Set DEEPSEEK_API_KEY for DeepSeek or OPENAI_API_KEY for OpenAI in your .env."
            )
        model = os.environ.get("OPENAI_MODEL", "gpt-3.5-turbo")
        temperature = float(os.environ.get("OPENAI_TEMPERATURE", "0.2"))
        max_tokens = int(os.environ.get("OPENAI_MAX_TOKENS", "3000"))
        return OpenAIConfig(api_key=key, model=model, temperature=temperature, max_tokens=max_tokens, base_url=None)


@dataclass(frozen=True)
class DataPaths:
    root: str
    water_data_dir: str

    @staticmethod
    def default(root: str) -> "DataPaths":
        water_dir = os.path.join(root, "data", "water_data")
        return DataPaths(root=root, water_data_dir=water_dir)
