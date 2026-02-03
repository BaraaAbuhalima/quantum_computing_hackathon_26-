from __future__ import annotations

import json
from typing import Dict, Any

from openai import OpenAI
from .config import OpenAIConfig


class AIClient:
    def __init__(self, cfg: OpenAIConfig):
        self.cfg = cfg
        if getattr(cfg, "base_url", None):
            self.client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
        else:
            self.client = OpenAI(api_key=cfg.api_key)

    def _estimate_tokens(self, messages):
        try:
            import tiktoken
            try:
                enc = tiktoken.encoding_for_model(self.cfg.model)
            except Exception:
                enc = tiktoken.get_encoding("cl100k_base")
            total = 0
            # Rough chat token accounting: 4 tokens per message + tokens for content, plus 2 for reply
            for m in messages:
                content = m.get("content") or ""
                total += 4 + len(enc.encode(content))
            total += 2
            return total
        except Exception:
            # Fallback: approximate by character length / 4
            total_chars = sum(len((m.get("content") or "")) for m in messages)
            return int(total_chars / 4)

    def propose_distribution(self, system_msg: str, user_payload: Dict[str, Any], schema_desc: Dict[str, Any]) -> Dict[str, Any]:
        messages = [
            {"role": "system", "content": system_msg},
            {
                "role": "user",
                "content": (
                    "Return ONLY a structured plan via tool call with the exact schema. "
                    "If any field is missing, the output is invalid.\n"
                    + json.dumps({"input": user_payload, "schema": schema_desc}, ensure_ascii=False)
                ),
            },
        ]

        # Define a function tool schema to force structured output
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "return_plan",
                    "description": "Return the water distribution plan matching the required schema exactly.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "transfers": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "from_locality_id": {"type": "integer"},
                                        "to_locality_id": {"type": "integer"},
                                        "flow_m3_day": {"type": "number"},
                                        "rationale": {"type": "string"},
                                    },
                                    "required": ["from_locality_id", "to_locality_id", "flow_m3_day"],
                                },
                            },
                            "source_allocations": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "water_source_id": {"type": "string"},
                                        "to_locality_id": {"type": "integer"},
                                        "allocated_m3_day": {"type": "number"},
                                        "rationale": {"type": "string"},
                                    },
                                    "required": ["water_source_id", "to_locality_id", "allocated_m3_day"],
                                },
                            },
                            "allocations_by_locality": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "locality_id": {"type": "integer"},
                                        "incoming_from_sources_m3_day": {"type": "number"},
                                        "incoming_from_localities_m3_day": {"type": "number"},
                                        "outgoing_transfers_m3_day": {"type": "number"},
                                        "secured_total_m3_day": {"type": "number"},
                                        "sources_breakdown": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "water_source_id": {"type": "string"},
                                                    "allocated_m3_day": {"type": "number"},
                                                },
                                                "required": ["water_source_id", "allocated_m3_day"],
                                            },
                                        },
                                    },
                                    "required": [
                                        "locality_id",
                                        "incoming_from_sources_m3_day",
                                        "incoming_from_localities_m3_day",
                                        "outgoing_transfers_m3_day",
                                        "secured_total_m3_day",
                                    ],
                                },
                            },
                            "summary": {
                                "type": "object",
                                "properties": {
                                    "total_unmet_demand_m3_day": {"type": "number"},
                                    "notes": {"type": "string"},
                                },
                                "required": ["total_unmet_demand_m3_day"],
                            },
                        },
                        "required": [
                            "transfers",
                            "source_allocations",
                            "allocations_by_locality",
                            "summary",
                        ],
                    },
                },
            }
        ]

        # Print prompt token estimate before sending
        try:
            tok = self._estimate_tokens(messages)
            print(f"Prompt tokens (est.): {tok} | model: {self.cfg.model}")
        except Exception:
            pass

        resp = self.client.chat.completions.create(
            model=self.cfg.model,
            messages=messages,
            temperature=self.cfg.temperature,
            tools=tools,
            tool_choice="required",
        )

        choice = resp.choices[0]
        # If the model returned a tool call, parse its arguments as the plan
        tool_calls = getattr(choice.message, "tool_calls", None)
        if tool_calls:
            # Concatenate or select first valid JSON args block
            for tc in tool_calls:
                args = tc.function.arguments or ""
                # Some SDKs return already-parsed dict; handle both
                if isinstance(args, dict):
                    return args
                try:
                    return json.loads(args)
                except Exception:
                    start = args.find("{")
                    end = args.rfind("}")
                    if start != -1 and end != -1 and end > start:
                        try:
                            return json.loads(args[start : end + 1])
                        except Exception:
                            pass
            raise ValueError("Tool call returned invalid JSON arguments.")

        # Fallback to content parsing (JSON only)
        content = choice.message.content or ""
        try:
            return json.loads(content)
        except Exception:
            start = content.find("{")
            end = content.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(content[start : end + 1])
            raise ValueError("Model output is not valid JSON and no JSON block could be extracted.")
