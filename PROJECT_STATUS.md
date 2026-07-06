# Project Status — Music Store Chatbot

## Current State
- ✅ Dependencies installed and working
- ✅ Demo runs successfully with all 4 sample prompts
- ✅ LLM integration complete — **hosted Claude or local endpoint**, auto-selected (`LLM_PROVIDER` / `ANTHROPIC_API_KEY`); a reviewer can run it with just an API key
- ✅ Built on LangChain `create_agent` (prebuilt ReAct loop) with a middleware stack (custom guardrail + 4 built-ins: call-limit, summarization, PII masking, model-retry) + an `InMemorySaver` checkpointer for conversation state
- ✅ 8 tools bound to LLM: purchase history, recommendations, inventory, artist lookup, genre catalog, genre browse, top sellers, store reference
- ✅ **Email-only identity gate** — personal data releases only on a verified email match; a name never unlocks PII and matching accounts are never enumerated (prevents impersonation)
- ✅ **Catalog tools** — browse albums by genre and rank top sellers (overall or per genre); honest that the catalog has no release dates
- ✅ **LangSmith tracing** verified live — named/tagged trace tree per turn (guardrail → model → tools); startup banner reports backend + tracing state
- ✅ Database context served on demand via `store_reference_tool` (kept out of the always-on prompt)
- ✅ Chinook database auto-downloads on first run
- ✅ LangSmith tracing configured via `.env`
- ✅ Robust customer name extraction (strips trailing punctuation/filler words)
- ✅ Name-matching ambiguity detection (asks user to clarify when multiple matches found)
- ✅ Hardcoded IP fallback removed (defaults to `localhost:8000`)
- ✅ PII-safe logging (TRACE_ENABLED/TRACE_RAW env vars, redacts sensitive args by default)
- ✅ **Topic guardrails** — `GuardrailMiddleware.before_model` keyword classifier, short-circuits off-topic queries before the LLM
- ✅ **Profanity easter egg** — 2-strike system with in-character warning and playful ban; the ban now **persists** (checkpointer) and stays sticky until `/clear`
- ✅ **LLM endpoint error handling** — graceful fallback message when the endpoint is unreachable
- ✅ **Conversation state via `InMemorySaver` checkpointer** — per-`thread_id` history + `profanity_strikes`; callers send only the new turn, follow-ups (e.g. supplying an email a turn later) just work; `/clear` = fresh thread
- ✅ **Context/token meter removed** — dropped `ContextTrimMiddleware` + token counting to cut complexity; the model's large window handles full history

## What's Done
- **LLM-driven agent**: LangChain `create_agent` (prebuilt ReAct loop) over `ChatOpenAI` (local llama.cpp), with cross-cutting concerns as middleware — no hand-rolled `StateGraph`
- **8 tools**: `purchase_history_tool`, `recommendation_tool`, `inventory_tool`, `artist_lookup_tool`, `genre_catalog_tool`, `browse_genre_tool`, `top_sellers_tool`, `store_reference_tool`
- **Model backend**: hosted Claude (`ChatAnthropic`) or a local OpenAI-compatible endpoint, chosen by `resolve_provider()` (`LLM_PROVIDER` / auto-detect from `ANTHROPIC_API_KEY`)
- **Email-only identity**: `resolve_customer_for_pii()` gates purchase history and recommendations on a unique, exact email match; a name never unlocks PII and matching accounts are never enumerated
- **Agent architecture**: `create_agent(model, tools, system_prompt, middleware=[GuardrailMiddleware, ModelCallLimitMiddleware, SummarizationMiddleware, PIIMiddleware, ModelRetryMiddleware], checkpointer=InMemorySaver())` — the prebuilt agent ⇄ tools loop plus one custom guardrail, four built-in middleware, and a checkpointer
- **Built-in middleware**: `ModelCallLimitMiddleware` (loop cap), `SummarizationMiddleware` (context management), `PIIMiddleware` (masks emails in replies only — input left intact so lookups work), `ModelRetryMiddleware` (retry + friendly-message on failure, replaced the custom `EndpointFallbackMiddleware`)
- **Conversation state**: `InMemorySaver` checkpointer holds per-`thread_id` `messages` + `profanity_strikes`; the runner sends only the new message each turn (no client-side transcript); `/clear` starts a fresh thread
- **Topic guardrails**: `GuardrailMiddleware.before_model` runs a lightweight keyword classifier and `jump_to="end"` on off-topic messages — no LLM call, no tool execution. Three-tier classification: hard off-topic blockers, on-topic signals, and ambiguous (defaults to on-topic to avoid false positives).
- **Profanity easter egg**: `_check_profanity()` (whole-word matching, so "hello" no longer trips "hell") runs inside `GuardrailMiddleware`. 1st strike → warning, 2nd strike → ban; strikes persist via the checkpointer, so the guardrail keeps the thread banned (`strikes >= 2`) until `/clear`.
- **Database context**: `database_context.md` served on demand via `store_reference_tool`, keeping ~650 tokens out of every prompt
- **Configuration**: all settings via env vars (`.env` / `.env.example`), loaded by `python-dotenv`; LangSmith tracing driven by its own env vars (no custom wiring)
- **Demo**: Sample mode (4 prompts) and interactive mode with per-thread memory and `/clear` + `/quit`
- **System prompt**: Slim — store overview + tool list + email-only identification procedure + catalog-tool guidance (full schema is on-demand via `store_reference_tool`)
- **Logging**: `trace_middleware` uses Python `logging` at DEBUG level; controlled by `TRACE_ENABLED` (off by default) and `TRACE_RAW` (logs raw args only when explicitly enabled)

## Known Issues & Future Improvements

### Reliability
- ~~**No error handling for unreachable LLM endpoint**~~ — ✅ Fixed: graceful fallback message when llama.cpp is down.

### Feature Requests
- ~~**Topic guardrails**~~ — ✅ Fixed: pre-check node with keyword classification, short-circuits off-topic queries.
- ~~**Profanity easter egg**~~ — ✅ Fixed: 2-strike system with in-character warning and playful ban.

## What's Next
- Consider upgrading guardrails from keyword-based MVP to LLM-based classification
- Consider adding a "forgot email" flow that guides users through disambiguation when they can't provide their email
- Consider adding a "profile lookup" tool that returns customer details (name, city, country) for verification

## Recent Changes
- 2026-07-06: **Deployable via LangGraph Platform** — added `langgraph.json` + `make_graph()` (builds the agent *without* a checkpointer so the server manages persistence). Run with `langgraph dev` (Studio, in-memory) or `langgraph up` (Docker: server + Postgres + Redis). `build_agent()` now takes an optional `checkpointer` (defaults to `InMemorySaver` for the CLI demo).
- 2026-07-06: **Strike count survives `/clear`** — `profanity_strikes` is now session-scoped, not conversation-scoped: `/clear` wipes thread history but `demo.py` carries the strike count forward (seeds it into the new thread), closing the loophole where an offender could `/clear` between swears to dodge the ban.
- 2026-07-06: **2nd profanity strike ends the session** — `demo.py` reads `profanity_strikes` off the invoke result and, at `>= 2`, prints the ban then breaks the loop (clean exit) — actually disconnecting the user instead of only threatening to.
- 2026-07-06: **Itemized purchase history** — `get_customer_purchase_history` now lists each recent invoice with the tracks purchased and their album (join through `InvoiceLine → Track → Album`), so "what albums did I buy?" is answerable instead of just totals/dates.
- 2026-07-06: **Adopted built-in middleware** — replaced custom `EndpointFallbackMiddleware` with the built-in `ModelRetryMiddleware` (retry + backoff, `on_failure` friendly message); added `ModelCallLimitMiddleware` (loop cap), `SummarizationMiddleware` (context management), and `PIIMiddleware` (email masked in replies only — `apply_to_input=False` so email lookups still work). `GuardrailMiddleware` stays custom (domain topic/profanity). Rejected input-side PII redaction (would break the identity gate).
- 2026-07-06: **System prompt scope-refusal** — the model now declines out-of-scope asks (write code/scripts, general questions, roleplay); the keyword guardrail stays as a cheap pre-filter for blatant cases.
- 2026-07-06: **Simplification pass** — (1) removed the context/token meter and the whole trimming subsystem (`ContextTrimMiddleware`, `trim_history`, `approx_tokens`, `MAX_CONTEXT_TOKENS`, `LLM_CONTEXT_WINDOW`); (2) added a LangGraph `InMemorySaver` checkpointer so conversation state (history + `profanity_strikes`) is owned per-`thread_id` and the runner sends only the new turn — this also makes the 2-strike ban actually persist; (3) dropped `configure_langsmith()` in favor of LangSmith's native `LANGSMITH_TRACING`/`LANGSMITH_API_KEY` env vars.
- 2026-07-06: **Model tuning env vars** — `LLM_TEMPERATURE`, `MAX_OUTPUT_TOKENS`, `ANTHROPIC_MODEL`, `LOCAL_MODEL` are now configurable; added `.env.example`.
- 2026-07-04: **Model backend choice** — added a hosted-Claude path (`ChatAnthropic`) alongside the local endpoint; `resolve_provider()`/`resolve_model()` auto-select from `LLM_PROVIDER`/`ANTHROPIC_API_KEY`, so a reviewer can run the demo with only an API key. A non-`claude-*` `LLM_MODEL` is ignored on the Anthropic path so a leftover local config can't break it.
- 2026-07-04: **Catalog tools** — `browse_genre_tool` (albums by genre, sales-ranked) and `top_sellers_tool` (best sellers overall/per genre); both honest that the catalog has no release dates. Sample prompts now showcase them.
- 2026-07-04: **Email-only identity gate** — replaced email-preferred/name-fallback with `resolve_customer_for_pii()`: PII releases only on a unique, exact email match; names never unlock PII and matching accounts are never enumerated (fixes the two-"Luis" impersonation).
- 2026-07-04: **LangSmith tracing** wired + documented — named/tagged runs per turn, startup banner reports backend + tracing; verified live end-to-end.
- 2026-07-04: **Context meter moved onto the input prompt** — one gauge at a time, refreshed each turn, instead of a trailing line after every reply.
- 2026-07-04: **Profanity matching fixed** — `_check_profanity` now matches whole words (regex tokenizer + set intersection), so "hello"/"class"/"password" no longer false-trigger.
- 2026-07-04: **Migrated to LangChain `create_agent`** — replaced the hand-rolled `StateGraph` (`guardrail_node`/`agent_node`/`tool_node` + routers) with the prebuilt ReAct loop plus three middleware: `GuardrailMiddleware` (`before_model`), `ContextTrimMiddleware` (`wrap_model_call`), `EndpointFallbackMiddleware` (`wrap_model_call`). Bumped requirements to LangChain 1.x.
- 2026-07-04: **Context budget + live meter** — `MAX_CONTEXT_TOKENS` history trimming, DB context moved to `store_reference_tool`, ASCII context meter in interactive mode, shared `/clear` + `/quit` reset.
- 2026-07-04: **Email-first customer identification** — replaced name-only lookup with email as primary identifier (100% unique across 59 customers)
- 2026-07-04: Added `_lookup_customer_by_email()` and `_lookup_customer_by_name()` functions in `support_bot.py`
- 2026-07-04: Updated `get_customer_purchase_history()` and `recommend_music_for_customer()` to try email first, then fall back to name
- 2026-07-04: Updated SYSTEM_PROMPT with explicit customer identification procedure (email preferred, name as fallback)
- 2026-07-04: Updated tool definitions to accept optional `customer_email` parameter
- 2026-07-04: Updated `database_context.md` with customer identification guidance
- 2026-07-04: Updated `demo.py` sample prompts to use email addresses
- 2026-07-04: Documented all 59 test accounts in README.md
- 2026-07-03: Migrated from keyword router to LLM-powered agent with llama.cpp integration
- 2026-07-03: Added 2 new tools (artist lookup, genre catalog) and database context injection
- 2026-07-03: Consolidated session memory into local MEMORY.md and PROJECT_STATUS.md
- 2026-07-03: Hardened name extraction, added ambiguity detection, removed hardcoded IP, replaced PII-printing with structured logging
