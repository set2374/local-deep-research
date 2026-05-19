"""Agent-facing ``fetch_content`` tool builders.

Public API:
    FETCH_MODES         — tuple of valid mode strings.
    build_fetch_tool()  — returns a LangChain ``@tool`` (or ``None`` when
                          mode == "disabled" so the caller can skip
                          registration).

Modes:
    disabled              — fetch tool is not registered with the agent.
    full                  — return the full extracted page text (legacy
                            behavior; can flood small-model context with
                            boilerplate / metadata enrichment).
    summary_focus         — LLM extracts only spans relevant to a focus
                            question the agent supplies per call.
    summary_focus_query   — same as above, but the prompt also includes
                            the original research query (passed in
                            programmatically by the strategy) so the
                            extractor can disambiguate vague focuses.

Each tool registers fetched URLs in the strategy's
``SearchResultsCollector`` for citation tracking, returning the result as
``[N] Title: ...\\nURL: ...\\n\\n<body>`` exactly like the original
in-strategy implementation, so downstream prompt formatting is unchanged.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool
from loguru import logger

from .prompts import SUMMARY_FOCUS_PROMPT, SUMMARY_FOCUS_QUERY_PROMPT

# Per-call timeouts and caps. Kept here rather than in the strategy file
# because they are properties of the fetch tool, not of agent
# orchestration.
CONTENT_FETCH_TIMEOUT = 30
CONTENT_MAX_LENGTH = 10_000

FETCH_MODES = (
    "disabled",
    "full",
    "summary_focus",
    "summary_focus_query",
)


def _coerce_approved_domains(value: Any) -> tuple[str, ...]:
    if isinstance(value, dict) and "value" in value:
        value = value["value"]
    if isinstance(value, str):
        raw_items = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = []
    domains: list[str] = []
    for item in raw_items:
        domain = str(item or "").strip().lower().removeprefix("*.")
        if domain and domain not in domains:
            domains.append(domain)
    return tuple(domains)


def _url_allowed(url: str, approved_domains: tuple[str, ...]) -> bool:
    if not approved_domains:
        return True
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.netloc or "").lower().removeprefix("www.")
    if not host:
        return False
    for domain in approved_domains:
        normalized = domain.lstrip(".")
        if normalized and (host == normalized or host.endswith(f".{normalized}")):
            return True
    return False


def _register_in_collector(
    collector: Any,
    url: str,
    title: str,
    snippet_source: str,
) -> int:
    """Register a fetched URL in the collector and return its 1-based citation index.

    If the URL was already tracked (via a prior search hit) the existing
    index is reused so the agent sees a stable citation per URL.
    """
    existing_idx = collector.find_by_url(url)
    if existing_idx is not None:
        return existing_idx
    snippet = snippet_source[:200].strip()
    if len(snippet_source) > 200:
        snippet += "..."
    start = collector.add_results(
        [{"title": title, "link": url, "snippet": snippet}],
        engine_name="fetch",
    )
    return start + 1


def _make_full_fetch_tool(collector: Any, approved_domains: tuple[str, ...]):
    @tool
    def fetch_content(url: str) -> str:
        """Download and read the full text content from a URL. Use when search snippets aren't detailed enough."""
        from local_deep_research.content_fetcher import ContentFetcher

        if not _url_allowed(url, approved_domains):
            return "Fetch rejected: URL is outside this run's approved source domains."
        try:
            with ContentFetcher(timeout=CONTENT_FETCH_TIMEOUT) as fetcher:
                result = fetcher.fetch(url, max_length=CONTENT_MAX_LENGTH)
                if result.get("status") == "success":
                    title = result.get("title", "")
                    content = result.get("content", "")
                    cite_idx = _register_in_collector(
                        collector, url, title, content
                    )
                    return (
                        f"[{cite_idx}] Title: {title}\nURL: {url}\n\n{content}"
                    )
                return f"Failed to fetch {url}: {result.get('error', 'unknown error')}"
        except Exception as exc:
            logger.exception("fetch_content tool error")
            return f"Error fetching {url}: {exc}"

    return fetch_content


def _make_summary_fetch_tool(
    collector: Any,
    model: BaseChatModel,
    overall_query: str | None,
    approved_domains: tuple[str, ...],
):
    """Build the summary-mode fetch tool.

    overall_query=None → focus-only prompt (``summary_focus`` mode).
    overall_query=str  → focus + overall-query prompt (``summary_focus_query``).
    """
    use_query = bool(overall_query)
    template = SUMMARY_FOCUS_QUERY_PROMPT if use_query else SUMMARY_FOCUS_PROMPT

    mode_label = "summary_focus_query" if use_query else "summary_focus"

    @tool
    def fetch_content(url: str, focus: str) -> str:
        """Fetch a URL and return only the spans of text relevant to ``focus``.
        Pass the specific question or claim you want answered as ``focus`` — the
        tool will quote relevant facts verbatim and discard unrelated content.
        """
        from local_deep_research.content_fetcher import ContentFetcher

        if not _url_allowed(url, approved_domains):
            return "Fetch rejected: URL is outside this run's approved source domains."
        try:
            with ContentFetcher(timeout=CONTENT_FETCH_TIMEOUT) as fetcher:
                result = fetcher.fetch(url, max_length=CONTENT_MAX_LENGTH)
                if result.get("status") != "success":
                    return f"Failed to fetch {url}: {result.get('error', 'unknown error')}"

                title = result.get("title", "")
                content = result.get("content", "")

            content = content[:CONTENT_MAX_LENGTH]
            if not content:
                return f"Failed to fetch {url}: no readable content"
            fmt_kwargs = {
                "focus": focus,
                "title": title,
                "url": url,
                "content": content,
            }
            if use_query:
                fmt_kwargs["overall_query"] = overall_query
            prompt = template.format(**fmt_kwargs)

            try:
                summary_msg = model.invoke(prompt)
                summary = getattr(
                    summary_msg, "content", str(summary_msg)
                ).strip()
            except Exception as exc:
                logger.exception("fetch_content summary LLM error")
                return f"Error summarizing {url}: {exc}"

            # Diagnostic log: per-fetch input/output for evaluating the
            # summariser. Single multi-line block so it's atomic per call
            # and easy to grep with ``grep -A1000 "[FETCH] mode="``.
            log_lines = [
                f"[FETCH] mode={mode_label} url={url}",
                f"[FETCH] focus: {focus}",
            ]
            if use_query:
                log_lines.append(f"[FETCH] overall_query: {overall_query}")
            log_lines.extend(
                [
                    f"[FETCH] title: {title}",
                    f"[FETCH] page_text ({len(content)} chars):",
                    content,
                    f"[FETCH] summary returned ({len(summary)} chars):",
                    summary or "(empty)",
                    "[FETCH] ---",
                ]
            )
            logger.info("\n".join(log_lines))

            cite_idx = _register_in_collector(
                collector, url, title, summary or content
            )
            return f"[{cite_idx}] Title: {title}\nURL: {url}\n\n{summary}"
        except Exception as exc:
            logger.exception("fetch_content tool error")
            return f"Error fetching {url}: {exc}"

    return fetch_content


def build_fetch_tool(
    mode: str,
    collector: Any,
    *,
    model: BaseChatModel | None = None,
    overall_query: str = "",
    approved_domains: Any = (),
):
    """Build the agent-facing ``fetch_content`` tool for *mode*.

    Returns ``None`` when ``mode == 'disabled'``; the caller should not
    register the tool with the agent in that case (and the system prompt
    should also drop the corresponding instruction line so the agent
    isn't told to use a tool that doesn't exist).
    """
    approved_domain_tuple = _coerce_approved_domains(approved_domains)
    if mode == "disabled":
        return None
    if mode == "full":
        return _make_full_fetch_tool(collector, approved_domain_tuple)
    if mode == "summary_focus":
        if model is None:
            raise ValueError("summary_focus fetch mode requires a model")
        return _make_summary_fetch_tool(
            collector,
            model,
            overall_query=None,
            approved_domains=approved_domain_tuple,
        )
    if mode == "summary_focus_query":
        if model is None:
            raise ValueError("summary_focus_query fetch mode requires a model")
        # Empty overall_query falls back to focus-only behaviour at format
        # time; we keep the *_query mode label so logs stay diagnostic.
        return _make_summary_fetch_tool(
            collector,
            model,
            overall_query=overall_query or None,
            approved_domains=approved_domain_tuple,
        )
    raise ValueError(
        f"Unknown fetch mode {mode!r}; expected one of {FETCH_MODES}"
    )


__all__ = ["FETCH_MODES", "build_fetch_tool"]
