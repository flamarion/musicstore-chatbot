import logging
import os
import re
import time
from functools import wraps
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    HumanInTheLoopMiddleware,
    InterruptOnConfig,
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    PIIMiddleware,
    SummarizationMiddleware,
    dynamic_prompt,
    hook_config,
)
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langsmith import traceable
from typing_extensions import NotRequired

from support_bot import (
    browse_albums_by_genre,
    ensure_chinook_database,
    find_artists_by_keyword,
    get_customer_purchase_history,
    get_inventory_snapshot,
    get_most_common_genres,
    recommend_music_for_customer,
    top_selling_albums,
)

# Configure logging — DEBUG level for middleware, INFO for everything else
logger = logging.getLogger("musicstore-chatbot")
logger.setLevel(logging.DEBUG)

# Load environment variables from .env file
load_dotenv()

# Set dummy API key for local LLM endpoints (llama.cpp, Ollama, etc.)
if not os.getenv("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = "sk-not-needed-for-local"


# Sensitive argument keys that should not be logged in full
_SENSITIVE_KEYS = frozenset({"customer_name", "customer_email", "keyword"})

# Trace control: TRACE_ENABLED=1 turns on middleware logging; TRACE_RAW=1
# (only when TRACE_ENABLED is also set) logs raw argument values instead of
# redacted ones.  Both default to off so no PII leaks to stdout.
_TRACE_ENABLED = os.getenv(
    "TRACE_ENABLED", "0").lower() in ("1", "true", "yes")
_TRACE_RAW = os.getenv("TRACE_RAW", "0").lower() in ("1", "true", "yes")


# Sampling temperature for the model (both backends).  Low by default so a
# support bot stays factual and consistent.
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))

# Optional hard cap on generated tokens (both backends).  Unset = backend
# default (local runs to EOS; Anthropic uses its client default).
_MAX_OUTPUT_TOKENS = os.getenv("MAX_OUTPUT_TOKENS", "").strip()
MAX_OUTPUT_TOKENS = int(_MAX_OUTPUT_TOKENS) if _MAX_OUTPUT_TOKENS else None

# Fallback model names when LLM_MODEL is unset (or unusable for the backend).
_DEFAULT_ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
_DEFAULT_LOCAL_MODEL = os.getenv("LOCAL_MODEL", "qwen3.6-35b-a3b")


def _redact_kwargs(kwargs: dict) -> dict:
    """Return a copy of *kwargs* with sensitive values replaced by '***'."""
    return {k: "***" if k in _SENSITIVE_KEYS else v for k, v in kwargs.items()}


def trace_middleware(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not _TRACE_ENABLED:
            return fn(*args, **kwargs)

        display_kwargs = kwargs if _TRACE_RAW else _redact_kwargs(kwargs)
        logger.debug(
            "[middleware] %s called with kwargs=%s",
            fn.__name__,
            display_kwargs,
        )
        start = time.monotonic()
        result = fn(*args, **kwargs)
        elapsed = time.monotonic() - start
        logger.debug("[middleware] %s completed in %.3fs",
                     fn.__name__, elapsed)
        return result

    return wrapped


@tool
@trace_middleware
def purchase_history_tool(customer_email: str = "", customer_name: str = "") -> str:
    """Look up a customer's recent orders, itemized with album/track titles.

    Returns the last few invoices and, under each, the tracks purchased and
    their album — so this answers "what did I buy?" / "what albums?", not just
    totals.  Requires the *customer_email* on the account — personal data is
    released only on a verified email match.  A name alone is not sufficient
    identification (names can collide), so pass *customer_name* only as extra
    context; if no email is supplied the tool asks for one.
    """
    return get_customer_purchase_history(customer_email=customer_email, customer_name=customer_name)


@tool
@trace_middleware
def recommendation_tool(customer_email: str = "", customer_name: str = "") -> str:
    """Recommend music genres for a customer based on what they have bought before.

    Requires the *customer_email* on the account — this reads personal purchase
    history, so it is released only on a verified email match.  For catalog-wide
    suggestions that need no identity, use top_sellers_tool or browse_genre_tool.
    """
    return recommend_music_for_customer(customer_email=customer_email, customer_name=customer_name)


@tool
@trace_middleware
def inventory_tool() -> str:
    """Give a quick inventory snapshot for the music store."""
    return get_inventory_snapshot()


@tool
@trace_middleware
def artist_lookup_tool(keyword: str) -> str:
    """Search the catalog for artists that match a keyword or style."""
    return find_artists_by_keyword(keyword)


@tool
@trace_middleware
def genre_catalog_tool() -> str:
    """Summarize the most common music genres in the catalog."""
    return get_most_common_genres()


@tool
@trace_middleware
def browse_genre_tool(genre: str) -> str:
    """Browse albums in a specific genre (e.g. 'Alternative & Punk', 'Rock', 'Jazz').

    The genre name is fuzzy-matched and albums are ranked by sales.  Note: the
    catalog has no release-date data, so this cannot sort by 'newest' — say so
    if the user asks for new arrivals, and offer top sellers instead.
    """
    return browse_albums_by_genre(genre)


@tool
@trace_middleware
def top_sellers_tool(genre: str = "") -> str:
    """List the best-selling albums, optionally within a genre.

    Pass a *genre* (e.g. 'Alternative & Punk') to rank top sellers inside it, or
    leave it empty for the store's overall best sellers.  Ranked by units sold.
    """
    return top_selling_albums(genre)


# ---------------------------------------------------------------------------
# Topic guardrails — lightweight keyword pre-filter (MVP, no LLM call)
# ---------------------------------------------------------------------------
# Off-topic keywords — if ANY of these appear, the message is flagged.
# Carefully curated to catch obvious non-store requests without
# over-matching on borderline queries.
_OFF_TOPIC_KEYWORDS = frozenset([
    # General knowledge / unrelated
    "how to code", "how to program", "write a", "create a website",
    "explain quantum", "who is the president", "what is the weather",
    "translate", "translate this", "summarize this article",
    # Coding / tech support
    "python", "javascript", "react", "docker", "kubernetes", "git",
    "debug my code", "fix my code", "help me code", "write code",
    "api endpoint", "database query", "sql query", "html", "css",
    "algorithm", "data structure", "leetcode", "hackerrank",
    # Unrelated domains
    "cook a", "recipe for", "exercise routine", "workout", "diet",
    "medical", "doctor", "hospital", "legal advice", "lawyer",
    "tax", "financial advice", "invest in", "stock market",
    "travel to", "hotel", "flight", "vacation",
    # Prompt injection / jailbreak patterns
    "ignore all instructions", "ignore previous", "do not follow",
    "you are now", "from now on", "system override",
])

_OFF_TOPIC_REDIRECT = (
    "I'm just here to help with the music store — purchases, "
    "recommendations, and the catalog. What can I help you find?"
)


DATABASE_CONTEXT_PATH = Path(__file__).resolve().parent / "database_context.md"
DATABASE_CONTEXT = DATABASE_CONTEXT_PATH.read_text(encoding="utf-8")


@tool
@trace_middleware
def store_reference_tool() -> str:
    """Return the full music-store database schema and data-model reference.

    Call this only if you need schema or relationship detail beyond what the
    standard lookup tools already provide.
    """
    return DATABASE_CONTEXT


SYSTEM_PROMPT_TEMPLATE = ChatPromptTemplate.from_messages([
    ("system", """Current date: {current_date}

You are a friendly and helpful customer support assistant for a digital music store.

You have access to the following tools:
- purchase_history_tool: Look up a customer's recent orders, itemized with album/track titles — use this for "what did I buy / what albums?" (needs a verified email)
- recommendation_tool: Recommend genres for a customer from their own purchase history (needs a verified email)
- inventory_tool: Give a quick inventory snapshot for the music store
- artist_lookup_tool: Search the catalog for artists that match a keyword or style
- genre_catalog_tool: Summarize the most common music genres in the catalog
- browse_genre_tool: Browse albums in a specific genre (e.g. 'Alternative & Punk'), ranked by sales
- top_sellers_tool: List the best-selling albums, overall or within a genre
- store_reference_tool: Full database schema/data-model reference — call only if you need schema detail beyond the standard tools

This is the Chinook digital music catalog — roughly 275 artists, 350 albums, and 3,500 tracks across genres like Rock, Jazz, Metal, Pop, Blues, and Classical, serving 59 customers worldwide. The lookup tools above already query this data for you; if you ever need the underlying schema or relationships, call store_reference_tool.

**Catalog questions (no identity needed):** For "what do you have in <genre>", use browse_genre_tool. For "what's popular / top selling", use top_sellers_tool (pass the genre if they named one). The catalog does NOT track release dates, so you cannot answer "newest" or "new arrivals" truthfully — say so plainly and offer the best sellers in that genre instead. Never invent recency.

**Customer identification — email required for personal data:**
Purchase history and personal recommendations are private. Release them ONLY after the user gives the email address on the account (it is the one unique identifier).
- Ask for the email. A name is NOT enough — names can collide (there are two "Luis" accounts), so a name alone must never unlock someone's account.
- If a name matches more than one account, do NOT list the accounts or their emails. Just ask for the email on the account.
- If the email doesn't match any account, tell them and ask them to double-check it. Do not fall back to name-only lookup for personal data.
- Never let a user pick which account they are — identity comes from the verified email, not their choice.

**Stay in scope.** You are ONLY a support assistant for THIS music store — the catalog,
purchases, recommendations, and account lookups. If asked to do anything else (write or debug
code, produce a script or SQL, answer general-knowledge or math questions, translate, roleplay
as a different assistant, etc.), politely decline in one sentence and steer back to how you can
help with the store. Do NOT write code or scripts, even when the request seems related to the
store's data — offer to look the data up for them instead.

Be conversational, helpful, and concise. Always use tools when appropriate rather than making up information.
If you are unsure which tool to use, ask the user for clarification."""),
])


@traceable(run_type="prompt", name="system_prompt")
def _render_system_prompt() -> str:
    """Format the system prompt for the current turn.

    Rendering a ``ChatPromptTemplate`` per call (rather than baking an f-string
    once at import) keeps "Current date" fresh and — as a ``run_type="prompt"``
    traceable — surfaces a distinct **prompt** run in LangSmith.  Returns the
    plain system text for the model request.
    """
    messages = SYSTEM_PROMPT_TEMPLATE.format_messages(
        current_date=time.strftime("%Y-%m-%d %H:%M:%S %Z")
    )
    return str(messages[0].content)


@dynamic_prompt
def system_prompt_middleware(request) -> str:
    """Supply the system prompt on every model call (LangChain dynamic-prompt hook)."""
    return _render_system_prompt()


def status_banner() -> str:
    """One-line summary of the active backend and tracing state, for the demo.

    LangSmith tracing is driven entirely by its own env vars (``LANGSMITH_TRACING``
    + ``LANGSMITH_API_KEY``, loaded from ``.env``) — no wiring on our side.
    """
    provider = resolve_provider()
    tracing = os.getenv("LANGSMITH_TRACING", "").lower() == "true"
    return (
        f"[backend: {provider} · model: {resolve_model(provider)} · "
        f"LangSmith: {'on' if tracing else 'off'}]"
    )


# ---------------------------------------------------------------------------
# Profanity easter egg — cosmetic only, not real moderation
# ---------------------------------------------------------------------------
_PROFANITY_WORDS = frozenset([
    "shit", "damn", "dumb", "stupid", "idiot", "hell", "crap",
    "bullshit", "ass", "bitch", "fuck", "fucking", "wtf",
])

# Word tokenizer for whole-word profanity matching (avoids "hell" in "hello").
_WORD_RE = re.compile(r"[a-z]+")

_PROFANITY_WARNING = (
    "Whoa, let's keep it friendly — one more and I'm ending this chat "
    "and hunting you down 😤"
)

_PROFANITY_BAN = (
    "That's it. You've been hunted down. Session terminated. "
    "Goodbye forever. 👋😈"
)


def _is_off_topic(text: str) -> bool:
    """Return True only for blatant off-topic input.

    A deliberately narrow keyword pre-filter: it blocks a message if any
    off-topic keyword appears, and lets everything else through.  Anything
    borderline is treated as on-topic (false positives are worse than false
    negatives); the system prompt's "stay in scope" rule is the real backstop.
    """
    lower = text.lower()
    return any(kw in lower for kw in _OFF_TOPIC_KEYWORDS)


def _check_profanity(text: str, strikes: int) -> str | None:
    """Return 'warning' or 'ban' if profanity detected, else None.

    Matches whole words only, so "hell" does not trip on "hello" nor "ass" on
    "class"/"password". 1st strike → warning, 2nd strike → ban.
    """
    words = set(_WORD_RE.findall(text.lower()))
    if words & _PROFANITY_WORDS:
        return "ban" if strikes >= 1 else "warning"
    return None


class GuardrailState(AgentState):
    """Agent state extended with the profanity-strike counter."""

    # Custom state property, owned by ProfanityGuardMiddleware and persisted per
    # thread by the checkpointer: 0 = clean, 1 = warned, 2 = banned.
    profanity_strikes: NotRequired[int]


def _latest_human_text(state) -> str | None:
    """Return the most recent HumanMessage content as a string, or None.

    Shared by both guardrail middlewares so each stays self-contained.  Coerces
    non-string content (e.g. multimodal parts) to ``str`` so the keyword checks
    can't blow up on unexpected message shapes.
    """
    last_human = next(
        (m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        None,
    )
    if last_human is None:
        return None
    text = last_human.content
    return text if isinstance(text, str) else str(text)


class ProfanityGuardMiddleware(AgentMiddleware):
    """Stateful profanity easter egg: warn on the 1st swear, ban on the 2nd.

    Owns the ``profanity_strikes`` counter declared on ``GuardrailState``; the
    checkpointer persists it per thread, and demo.py carries the count across
    ``/clear``, so a fresh thread can't wipe the ban (no dodging by clearing
    between swears).  As a ``before_model`` hook it short-circuits with
    ``jump_to="end"``, so a blocked message costs no LLM or tool call.  It runs
    ahead of the topic guard so a ban trumps everything else.
    """

    state_schema = GuardrailState

    @hook_config(can_jump_to=["end"])
    def before_model(self, state, runtime):
        text = _latest_human_text(state)
        if text is None:
            return None

        strikes = state.get("profanity_strikes", 0)

        # Already banned (2 strikes) — stay banned.
        if strikes >= 2:
            return {"jump_to": "end", "messages": [AIMessage(content=_PROFANITY_BAN)]}

        result = _check_profanity(text, strikes)
        if result == "ban":
            return {
                "jump_to": "end",
                "messages": [AIMessage(content=_PROFANITY_BAN)],
                "profanity_strikes": strikes + 1,
            }
        if result == "warning":
            return {
                "jump_to": "end",
                "messages": [AIMessage(content=_PROFANITY_WARNING)],
                "profanity_strikes": strikes + 1,
            }
        return None


class TopicGuardMiddleware(AgentMiddleware):
    """Stateless topic pre-filter: short-circuit blatant off-topic input.

    A cheap keyword classifier that redirects obvious non-store requests before
    any LLM call.  Intentionally biased toward false-negatives (lets borderline
    input through); the system prompt's "stay in scope" rule is the real
    backstop.  Like the profanity guard, it's a ``before_model`` hook that can
    ``jump_to="end"`` so a redirect costs nothing.
    """

    @hook_config(can_jump_to=["end"])
    def before_model(self, state, runtime):
        text = _latest_human_text(state)
        if text is None:
            return None
        if _is_off_topic(text):
            return {"jump_to": "end", "messages": [AIMessage(content=_OFF_TOPIC_REDIRECT)]}
        return None


def _endpoint_down_message(exc: Exception) -> str:
    """Friendly reply when the model is unreachable after retries.

    Passed as ``on_failure`` to the built-in ``ModelRetryMiddleware`` — it wraps
    the returned string in an ``AIMessage``, so a down endpoint yields this
    instead of a crash (replacing the old hand-rolled EndpointFallbackMiddleware).
    """
    logger.warning("LLM invocation failed after retries: %s", exc)
    return (
        "I'm having trouble reaching my brain right now — "
        "the language model is unavailable. Please try again in a moment."
    )


# ---------------------------------------------------------------------------
# Human-in-the-loop consent gate for personal data (built-in middleware)
# ---------------------------------------------------------------------------
# The two tools that read a customer's personal data.  Before either runs, the
# built-in HumanInTheLoopMiddleware pauses the agent (interrupt) so the user can
# explicitly consent to their email being used for the lookup — GDPR-style
# "purpose consent" on top of the email-only identity gate.  The other six tools
# (inventory, artist, genre, browse, top-sellers, reference) touch no personal
# data and are auto-approved.
# Per-tool consent purpose, keyed by tool name so it stays in sync with the set.
_PII_CONSENT_PURPOSE = {
    "purchase_history_tool": "look up your personal purchase history",
    "recommendation_tool": "build recommendations from your personal purchase history",
}
_PII_CONSENT_TOOLS = tuple(_PII_CONSENT_PURPOSE)


def _consent_description(tool_call, state, runtime) -> str:
    """Render the consent prompt shown to the user before a PII lookup runs.

    Used as the ``description`` factory in the tool's ``InterruptOnConfig``; the
    string is surfaced in the interrupt payload (CLI prompt or agent-chat-ui card).
    """
    email = (tool_call["args"].get("customer_email") or "").strip()
    purpose = _PII_CONSENT_PURPOSE[tool_call["name"]]
    return (
        f"🔒 Consent needed — I'd like to use the email {email} to {purpose}. "
        "Approve to proceed, or reject to cancel."
    )


def _uses_an_email(request) -> bool:
    """Only interrupt when an email is actually about to be used.

    Passed as the ``when`` predicate: if the model calls a PII tool without an
    email, there's nothing to consent to — the tool just asks for one — so we
    skip the interrupt and let it through.
    """
    return bool((request.tool_call["args"].get("customer_email") or "").strip())


def _pii_consent_middleware() -> HumanInTheLoopMiddleware:
    """Build the consent gate: approve/reject before either PII tool executes."""
    config = InterruptOnConfig(
        allowed_decisions=["approve", "reject"],
        description=_consent_description,
        when=_uses_an_email,
    )
    return HumanInTheLoopMiddleware(
        interrupt_on={name: config for name in _PII_CONSENT_TOOLS},
        description_prefix="Personal-data access requires your consent",
    )


# Which backend serves the LLM.  Auto-detected unless LLM_PROVIDER is set:
#   - "anthropic" — hosted Claude (langchain-anthropic, needs ANTHROPIC_API_KEY).
#     Zero-setup path for a reviewer: set the key and run, no local server.
#   - "local"     — any OpenAI-compatible endpoint (llama.cpp / Ollama / vLLM)
#     at LLM_ENDPOINT.  The original offline default.
# Auto: use Anthropic when ANTHROPIC_API_KEY is present, otherwise the endpoint.
# Model names and temperature come from the env constants defined up top.
def resolve_provider() -> str:
    """Return 'anthropic' or 'local' — the backend the agent will use."""
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    if provider in ("anthropic", "claude"):
        return "anthropic"
    if provider in ("local", "openai"):
        return "local"
    return "anthropic" if os.getenv("ANTHROPIC_API_KEY") else "local"


def resolve_model(provider: str) -> str:
    """Return the model name for *provider*, honoring LLM_MODEL when it fits.

    For the hosted path, LLM_MODEL is honored only if it names a Claude model —
    it often holds a local model name (e.g. the .env default), and passing that
    to the hosted client would fail, so a leftover local config can't break the
    zero-setup reviewer path.
    """
    override = os.getenv("LLM_MODEL", "").strip()
    if provider == "anthropic":
        return override if override.lower().startswith("claude") else _DEFAULT_ANTHROPIC_MODEL
    return override or _DEFAULT_LOCAL_MODEL


def build_llm():
    """Build the chat model for the agent — hosted Claude or a local endpoint.

    The Anthropic path lets a reviewer run the demo with only an API key (no
    local inference server); the local path keeps the original
    OpenAI-compatible endpoint for fully offline use.  See ``resolve_provider``.
    """
    provider = resolve_provider()
    model = resolve_model(provider)

    kwargs = {"temperature": LLM_TEMPERATURE}
    if MAX_OUTPUT_TOKENS is not None:
        kwargs["max_tokens"] = MAX_OUTPUT_TOKENS

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic  # lazy: optional dependency

        logger.info("LLM backend: Anthropic '%s'", model)
        return ChatAnthropic(model=model, **kwargs)

    endpoint = os.getenv("LLM_ENDPOINT", "http://localhost:8000/v1")
    logger.info("LLM backend: local '%s' at %s", model, endpoint)
    return ChatOpenAI(model=model, base_url=endpoint, **kwargs)


# Sentinel: callers pass checkpointer=None to omit persistence (LangGraph Server
# and `langgraph dev` inject their own — Postgres or in-memory), while the default
# keeps the CLI demo's in-memory checkpointer.
_DEFAULT_CHECKPOINTER = object()


def build_agent(checkpointer: Any = _DEFAULT_CHECKPOINTER):
    ensure_chinook_database()

    llm = build_llm()

    if checkpointer is _DEFAULT_CHECKPOINTER:
        checkpointer = InMemorySaver()

    tools = [
        purchase_history_tool,
        recommendation_tool,
        inventory_tool,
        artist_lookup_tool,
        genre_catalog_tool,
        browse_genre_tool,
        top_sellers_tool,
        store_reference_tool,
    ]

    # The prebuilt ReAct loop (agent <-> tools) plus a middleware stack — two
    # custom guardrails and four LangChain built-ins.  Order is outer→inner:
    #   - ProfanityGuardMiddleware (custom)  : stateful 2-strike profanity gate; runs first so a
    #                                          ban trumps everything, before any LLM call
    #   - TopicGuardMiddleware     (custom)  : stateless off-topic keyword pre-filter
    #   - ModelCallLimitMiddleware (builtin) : cap the ReAct loop so a turn can't spin forever
    #   - SummarizationMiddleware  (builtin) : condense old history past a token budget (rarely
    #                                          fires at this window; idiomatic context management)
    #   - PIIMiddleware            (builtin) : redact emails in the bot's REPLIES only — input is
    #                                          left intact so the email-required lookups still work
    #   - ModelRetryMiddleware     (builtin) : retry with backoff, then _endpoint_down_message
    #   - HumanInTheLoopMiddleware (builtin) : pause for user consent before a PII tool runs;
    #                                          interrupts the graph and resumes on approve/reject
    # The system prompt is supplied by system_prompt_middleware (dynamic_prompt) instead of the
    # create_agent system_prompt= arg, so it renders a ChatPromptTemplate fresh each turn (keeps
    # "Current date" current) and shows as a prompt run in traces.
    # An InMemorySaver checkpointer persists per-thread state (messages AND
    # profanity_strikes) and the HITL interrupt, so callers send only the new turn,
    # the 2-strike ban fires across separate messages, and a paused consent survives.
    return create_agent(
        model=llm,
        tools=tools,
        middleware=[
            ProfanityGuardMiddleware(),
            TopicGuardMiddleware(),
            system_prompt_middleware,
            ModelCallLimitMiddleware(run_limit=8, exit_behavior="end"),
            SummarizationMiddleware(model=llm, trigger=("tokens", 8000), keep=("messages", 20)),
            PIIMiddleware("email", strategy="redact", apply_to_input=False, apply_to_output=True),
            ModelRetryMiddleware(max_retries=2, on_failure=_endpoint_down_message),
            _pii_consent_middleware(),
        ],
        checkpointer=checkpointer,
    )


def make_graph():
    """Entry point for LangGraph Server / Studio — referenced by ``langgraph.json``.

    Built WITHOUT a checkpointer: the platform manages persistence (Postgres in
    the Docker stack, in-memory under ``langgraph dev``), so passing our own would
    be redundant. The CLI demo keeps ``build_agent()``'s ``InMemorySaver``.
    """
    return build_agent(checkpointer=None)


if __name__ == "__main__":
    # Add a console handler so DEBUG logs are visible
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    app = build_agent()
    config = {"configurable": {"thread_id": "smoke-test"}}
    for prompt in ["Recommend music for Luis", "Show my invoice history for Luis"]:
        print(f"> {prompt}")
        output = app.invoke({"messages": [HumanMessage(content=prompt)]}, config=config)
        print(output["messages"][-1].content)
        print()
