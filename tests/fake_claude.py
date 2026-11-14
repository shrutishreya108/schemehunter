"""FakeClaude: canned Claude responses for tests. No network, ever.

Responses are queued per *purpose* ("loop", "explain", "explain_cited", "scan", "classify"), so a script
only lists what matters. Scripts live in tests/fixtures/claude/<name>.yaml:

    loop:
      - content:
          - {tool: match_schemes, input: {}}
      - content:
          - {text: "hello"}
    explain:
      - content: [{tool: explanation, input: {en: "...", hi: "..."}}]

Block shapes: {text: str, citations?: [{page, cited_text}]} or {tool: name, input: {...}}.
stop_reason defaults to "tool_use" when a tool block is present, else "end_turn". usage defaults to
DEFAULT_USAGE so cost accounting sees non-zero numbers.

When a queue is empty: "scan" → "[]" (no scheme names), "explain" → a generic explanation, "explain_cited"
→ an answer citing the first page document in the request; "loop" fails the test (script is incomplete).
"""

from itertools import count
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

FIXTURES = Path(__file__).parent / "fixtures" / "claude"
DEFAULT_USAGE = {
    "input_tokens": 1200,
    "output_tokens": 150,
    "cache_read_input_tokens": 4000,
    "cache_creation_input_tokens": 0,
}
_ids = count(1)


def block(spec: dict) -> SimpleNamespace:
    if "tool" in spec:
        return SimpleNamespace(
            type="tool_use",
            id=f"toolu_{next(_ids):04d}",
            name=spec["tool"],
            input=dict(spec.get("input") or {}),
        )
    cites = [
        SimpleNamespace(
            type="char_location",
            cited_text=c["cited_text"],
            document_title=c.get("document_title") or f"guidelines page {c['page']}",
            document_index=c.get("document_index"),
        )
        for c in spec.get("citations", [])
    ]
    return SimpleNamespace(type="text", text=spec["text"], citations=cites or None)


def response(spec: dict, model: str) -> SimpleNamespace:
    content = [block(b) for b in spec["content"]]
    stop = spec.get("stop_reason") or (
        "tool_use" if any(b.type == "tool_use" for b in content) else "end_turn"
    )
    return SimpleNamespace(
        id=f"msg_{next(_ids):04d}",
        model=model,
        content=content,
        stop_reason=stop,
        usage=SimpleNamespace(**{**DEFAULT_USAGE, **spec.get("usage", {})}),
    )


def first_page_title(messages: list[dict]) -> str:
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "document":
                    return part.get("title", "guidelines page 1")
    return "guidelines page 1"


class FakeClaude:
    def __init__(self, scripts: dict[str, list[dict]] | None = None):
        self.queues: dict[str, list[dict]] = {}
        self.calls: list[dict[str, Any]] = []
        if scripts:
            self.add(scripts)

    def add(self, scripts: dict[str, list[dict]]) -> "FakeClaude":
        for purpose, items in scripts.items():
            self.queues.setdefault(purpose, []).extend(items)
        return self

    def load(self, name: str) -> "FakeClaude":
        return self.add(yaml.safe_load((FIXTURES / f"{name}.yaml").read_text(encoding="utf-8")))

    def calls_for(self, purpose: str) -> list[dict]:
        return [c for c in self.calls if c["purpose"] == purpose]

    def create(
        self,
        *,
        purpose: str,
        model: str,
        max_tokens: int,
        system: Any,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
    ) -> SimpleNamespace:
        self.calls.append(
            {
                "purpose": purpose,
                "model": model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [dict(m) for m in messages],
                "tools": tools,
                "tool_choice": tool_choice,
            }
        )
        queue = self.queues.get(purpose)
        if queue:
            return response(queue.pop(0), model)
        if purpose == "scan":
            return response(
                {
                    "content": [{"text": "[]"}],
                    "usage": {"input_tokens": 80, "output_tokens": 5, "cache_read_input_tokens": 0},
                },
                model,
            )
        if purpose == "explain":
            return response(
                {
                    "content": [
                        {
                            "tool": "explanation",
                            "input": {
                                "en": "You qualify to apply. The rules engine checked every rule.",
                                "hi": "आप आवेदन करने के योग्य हैं। नियम जाँच ने हर नियम देखा।",
                            },
                        }
                    ]
                },
                model,
            )
        if purpose == "explain_cited":
            title = first_page_title(messages)
            return response(
                {
                    "content": [
                        {"text": "EN: You qualify to apply. "},
                        {
                            "text": "The applicant must be above 18 years of age.",
                            "citations": [
                                {"cited_text": "above 18 years of age", "document_title": title, "page": 0}
                            ],
                        },
                        {"text": " HI: आप आवेदन करने के योग्य हैं।"},
                    ]
                },
                model,
            )
        raise AssertionError(f"FakeClaude: no scripted response left for purpose={purpose!r}")
