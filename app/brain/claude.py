"""Every Claude call goes through here (LLD §7.4 + v0.3 budget rules).

- Prompt caching: system text + Rule Book summary (cache_control on the last system block), all tool
  definitions (cache_control on the last tool), and guideline page documents. The case file is the only
  per-run content.
- Model routing: MODEL_REASON for the agent loop, explain_match and document vision; MODEL_CHEAP for intent
  classification and the unknown-scheme scanner.
- max_tokens: MAX_TOKENS_LOOP (1200) per loop step; MAX_TOKENS_LONG (2500) for explain_match / narrative.
- Every response's usage is added to a UsageMeter so the run's estimated cost lands in brain_runs.

Only app/brain may import anthropic (tests/test_boundaries.py). Tests install FakeClaude via set_client().
"""

import copy
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from app.config import get_settings
from app.costs import UsageMeter

PROMPTS = Path(__file__).parent / "prompts"
EPHEMERAL = {"type": "ephemeral"}


class ClaudeError(RuntimeError):
    """The model answered, but not in the shape we asked for."""


class ClaudeClient(Protocol):
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
    ) -> Any: ...


class AnthropicClient:
    """Thin wrapper over the SDK. `purpose` is ours (for routing/fakes) and is not sent to the API."""

    def __init__(self, api_key: str):
        import anthropic
        s = get_settings()
        # A plain number: the SDK rejects httpx.Timeout objects (it uses its own httpx2 types).
        self.raw = anthropic.Anthropic(
            api_key=api_key,
            timeout=float(s.CLAUDE_TIMEOUT_S),
            max_retries=s.CLAUDE_MAX_RETRIES,
        )

    def create(self, *, purpose: str, **kwargs: Any) -> Any:
        return self.raw.messages.create(**{k: v for k, v in kwargs.items() if v is not None})


_client: ClaudeClient | None = None


def set_client(client: ClaudeClient | None) -> None:
    global _client
    _client = client


def get_client() -> ClaudeClient:
    global _client
    if _client is None:
        key = get_settings().ANTHROPIC_API_KEY
        if not key:
            raise ClaudeError("ANTHROPIC_API_KEY is not set")
        _client = AnthropicClient(key)
    return _client


# ---------------------------------------------------------------- cached blocks


@lru_cache
def prompt(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def rulebook_summary(schemes: list[dict]) -> str:
    """Stable text (sorted by id) so the cached block never changes between runs."""
    lines = ["Rule Book (the only schemes you may mention):"]
    for s in sorted(schemes, key=lambda s: s["id"]):
        text = (s.get("benefit") or {}).get("text") or {}
        scope = "central" if s["level"] == "central" else f"state: {s.get('state')}"
        lines.append(
            f"- {s['id']}: {s['name']['en']} / {s['name']['hi']} ({scope}; {s['benefit']['type']}; "
            f"apply via {s['apply_mode']}). {text.get('en', '')}"
        )
    return "\n".join(lines)


def build_system_blocks(system_text: str, rulebook: str) -> list[dict]:
    return [
        {"type": "text", "text": system_text},
        {"type": "text", "text": rulebook, "cache_control": EPHEMERAL},
    ]


def cached_tools(tools: list[dict]) -> list[dict]:
    """Copy of the tool list with cache_control on the last tool (caches all definitions)."""
    out = copy.deepcopy(tools)
    if out:
        out[-1]["cache_control"] = EPHEMERAL
    return out


# ---------------------------------------------------------------- calls


def call(
    purpose: str,
    model: str,
    max_tokens: int,
    system: Any,
    messages: list[dict],
    meter: UsageMeter,
    tools: list[dict] | None = None,
    tool_choice: dict | None = None,
    client: ClaudeClient | None = None,
) -> Any:
    resp = (client or get_client()).create(
        purpose=purpose,
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
    )
    meter.add(model, resp.usage)
    return resp


def loop_call(
    system: list[dict],
    tools: list[dict],
    messages: list[dict],
    meter: UsageMeter,
    client: ClaudeClient | None = None,
) -> Any:
    s = get_settings()
    return call(
        "loop", s.MODEL_REASON, s.MAX_TOKENS_LOOP, system, messages, meter, tools=tools, client=client
    )


def tool_input(resp: Any, name: str) -> dict:
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == name:
            return dict(block.input)
    raise ClaudeError(f"expected a {name} tool call")


def forced_tool_call(
    tool: dict,
    system: Any,
    messages: list[dict],
    meter: UsageMeter,
    *,
    purpose: str,
    model: str | None = None,
    max_tokens: int | None = None,
    client: ClaudeClient | None = None,
) -> dict:
    """Structured output: define a tool with the JSON schema and force it."""
    s = get_settings()
    resp = call(
        purpose,
        model or s.MODEL_REASON,
        max_tokens or s.MAX_TOKENS_LONG,
        system,
        messages,
        meter,
        tools=[{**tool, "cache_control": EPHEMERAL}],
        tool_choice={"type": "tool", "name": tool["name"]},
        client=client,
    )
    return tool_input(resp, tool["name"])


def page_documents(pages: list[tuple[int, str]], scheme_name: str) -> list[dict]:
    """One plain-text document per guideline page; the title encodes the page number."""
    docs = [
        {
            "type": "document",
            "source": {"type": "text", "media_type": "text/plain", "data": text},
            "title": f"{scheme_name} guidelines page {page}",
            "citations": {"enabled": True},
        }
        for page, text in pages
    ]
    if docs:
        docs[-1]["cache_control"] = EPHEMERAL
    return docs


_PAGE_IN_TITLE = re.compile(r"page (\d+)$")
_EN_HI = re.compile(r"EN:\s*(?P<en>.*?)\s*HI:\s*(?P<hi>.*)\Z", re.DOTALL)


def citation_page(citation: Any, pages: list[tuple[int, str]]) -> int | None:
    title = getattr(citation, "document_title", None) or ""
    m = _PAGE_IN_TITLE.search(title)
    if m:
        return int(m.group(1))
    idx = getattr(citation, "document_index", None)
    return pages[idx][0] if isinstance(idx, int) and 0 <= idx < len(pages) else None


def parse_cited_answer(resp: Any, pages: list[tuple[int, str]]) -> dict:
    """Join text blocks, split "EN: … HI: …", collect citations as [{page, cited_text}] (deduped)."""
    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text")
    m = _EN_HI.search(text)
    if not m:
        raise ClaudeError("cited answer missing EN:/HI: sections")
    citations, seen = [], set()
    for block in resp.content:
        for c in getattr(block, "citations", None) or []:
            item = (citation_page(c, pages), getattr(c, "cited_text", "").strip())
            if item[1] and item not in seen:
                seen.add(item)
                citations.append({"page": item[0], "cited_text": item[1]})
    return {"en": m.group("en").strip(), "hi": m.group("hi").strip(), "citations": citations}


def citations_call(
    pages: list[tuple[int, str]],
    scheme_name: str,
    instruction: str,
    meter: UsageMeter,
    client: ClaudeClient | None = None,
) -> dict:
    """Explain over guideline pages with citations enabled → {en, hi, citations:[{page, cited_text}]}."""
    s = get_settings()
    content = [*page_documents(pages, scheme_name), {"type": "text", "text": instruction}]
    system = [{"type": "text", "text": prompt("explain_match.md"), "cache_control": EPHEMERAL}]
    resp = call(
        "explain_cited",
        s.MODEL_REASON,
        s.MAX_TOKENS_LONG,
        system,
        [{"role": "user", "content": content}],
        meter,
        client=client,
    )
    return parse_cited_answer(resp, pages)


def cheap_scheme_extractor(meter: UsageMeter, client: ClaudeClient | None = None):
    """MODEL_CHEAP lists scheme names mentioned in a text; the guard subtracts Rule Book names."""
    s = get_settings()

    def extract(body: str) -> list[str]:
        resp = call(
            "scan",
            s.MODEL_CHEAP,
            200,
            [
                {
                    "type": "text",
                    "text": "List every government scheme, yojana or programme name mentioned in "
                    "the user's text. Reply with a JSON array of strings only; [] if none.",
                }
            ],
            [{"role": "user", "content": body}],
            meter,
            client=client,
        )
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text")
        try:
            names = json.loads(text[text.find("[") : text.rfind("]") + 1] or "[]")
        except json.JSONDecodeError:
            return []
        return [n for n in names if isinstance(n, str)]

    return extract
