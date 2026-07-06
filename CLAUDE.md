# CLAUDE.md — agent context for this repo

Context for AI coding tools (Claude Code and, by convention, other agents) working in this
project. Keep it short; link out for detail.

## What this is

A small **LangChain + LangGraph + LangSmith** demo: a customer-support chatbot for a digital
music store (the Chinook sample DB). Built as a take-home technical task, so favor
**clarity and pragmatism over ceremony** — this is a demo, not production. No formal specs;
keep the file set small (reuse existing docs rather than adding new ones).

## Architecture (one breath)

Built with LangChain `create_agent` (prebuilt ReAct loop, agent ⇄ tools) + a middleware stack (1 custom + 4 built-in) + an `InMemorySaver` checkpointer — **no hand-rolled `StateGraph`**:
- `GuardrailMiddleware.before_model` (**custom**) — keyword topic classifier + 2-strike profanity easter egg; `jump_to="end"` before any LLM call.
- `ModelCallLimitMiddleware` (built-in) — `run_limit=8`, `exit_behavior="end"`; bounds the ReAct loop.
- `SummarizationMiddleware` (built-in) — condenses history past `("tokens", 8000)`, keeps recent 20; idiomatic context mgmt (rarely fires).
- `PIIMiddleware("email", strategy="mask", apply_to_input=False, apply_to_output=True)` (built-in) — masks emails in **replies only**; input stays intact so lookups work. **Do not enable `apply_to_input`** — it would redact the email before tools see it and break the identity gate.
- `ModelRetryMiddleware(on_failure=_endpoint_down_message)` (built-in) — retry with backoff, then friendly message (replaced the old custom `EndpointFallbackMiddleware`).
- `InMemorySaver` checkpointer — persists per-`thread_id` state (`messages` + `profanity_strikes`); callers send only the new turn.
- 8 tools (7 lookups + `store_reference_tool`) run against the Chinook SQLite DB. Catalog lookups include `browse_genre_tool` and `top_sellers_tool`.
- Model backend is **hosted Claude or a local endpoint**, chosen by `resolve_provider()` (`LLM_PROVIDER` / auto-detect from `ANTHROPIC_API_KEY`).

Full diagram and component table: [README.md](README.md#architecture).

## Key files

| File | Purpose |
|---|---|
| [app.py](app.py) | `create_agent` + guardrail/fallback middleware, `InMemorySaver` checkpointer, 8 tools, model-backend selection, system prompt, tracing middleware |
| [support_bot.py](support_bot.py) | Chinook DB access + the tool implementations, catalog queries, email-only identity gate (`resolve_customer_for_pii`) |
| [demo.py](demo.py) | Sample (`--sample`) and interactive runners |
| [langgraph.json](langgraph.json) | LangGraph Server/Studio config → `app.py:make_graph` (deploy the graph via `langgraph dev` / `langgraph up`) |
| [database_context.md](database_context.md) | Schema/data insights, served on demand via `store_reference_tool` (not baked into every prompt) |
| [README.md](README.md) | User-facing overview, architecture, 59 test accounts, config |
| [PROJECT_STATUS.md](PROJECT_STATUS.md) | Current state, roadmap, recent changes |
| [MEMORY.md](MEMORY.md) | Environment, commands, lessons learned |

## Run it

```bash
source ~/.virtualenvs/langchain/bin/activate   # deps live here, NOT a project-local venv
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...   # hosted Claude (zero local setup); else uses LLM_ENDPOINT
python demo.py --sample     # batch mode (4 scripted prompts)
python demo.py              # interactive mode (/quit, /clear)
TRACE_ENABLED=1 python demo.py   # middleware logging (redacts PII by default)
```

## Conventions & gotchas

- **LLM backend** — hosted Claude (`ChatAnthropic`) or a local OpenAI-compatible endpoint
  (`ChatOpenAI` → llama.cpp/Ollama at `LLM_ENDPOINT`), via `build_llm()`.
- **Conversation state lives in the `InMemorySaver` checkpointer**, keyed by `thread_id` in the
  invoke config — NOT client-side. Callers send only the new message each turn; LangGraph
  restores the rest. `/clear` = new `thread_id`. In-memory, so state resets on process restart.
- **Dummy `OPENAI_API_KEY`** is set automatically for local endpoints — the OpenAI client
  requires *some* key even when the server ignores it. (Harmless on the Anthropic path.)
- **Console tracing off by default** (`TRACE_ENABLED`/`TRACE_RAW`) to avoid leaking PII to stdout.
  **LangSmith** tracing is driven by its own env vars (`LANGSMITH_TRACING`/`LANGSMITH_API_KEY`) —
  we add no wiring; don't reinvent it.
- **Customer identification is email-ONLY for personal data** — purchase history and
  recommendations release only on a unique, exact email match (`resolve_customer_for_pii`).
  A name never unlocks PII and the bot never enumerates matching accounts (prevents the
  two-"Luis" impersonation). Keep the system prompt's email-only procedure.
- **Scope control is two layers.** `GuardrailMiddleware` is a cheap keyword pre-filter that
  short-circuits *blatant* off-topic input before any LLM call — intentionally biased toward
  false-negatives (lets borderline through, e.g. "could you write me a script" slips past it).
  The real backstop is the **system prompt's "Stay in scope" rule**, which makes the model
  itself decline out-of-scope asks (code, general questions, roleplay). Don't try to make the
  keyword list exhaustive; harden the prompt instead. The profanity ban is a cosmetic easter egg.
- **Config** via `.env` (`python-dotenv`): `LLM_PROVIDER`/`ANTHROPIC_API_KEY`, `LLM_ENDPOINT`,
  `LLM_MODEL`, `LLM_TEMPERATURE`, `MAX_OUTPUT_TOKENS`, LangSmith creds. See `.env.example`.

## Notes

- **Profanity ban: persists, survives `/clear`, and ends the session.** `profanity_strikes` lives
  in the checkpointer per thread, so it survives across turns. `demo.py` treats the strike count as
  **session-scoped, not conversation-scoped**: `/clear` rotates the thread (wiping history) but the
  client carries `strikes` forward and seeds it into the new thread — so an offender can't dodge the
  ban by clearing between swears. On the 2nd strike, `demo.py` prints the ban then **breaks the loop**,
  actually disconnecting the user (exit 0). (Persistence was previously a known gap — no checkpointer.)
- **Context management is the built-in `SummarizationMiddleware`** (added after the custom
  `ContextTrimMiddleware`/token-meter was removed). It condenses old history past a token budget
  instead of dropping it; with the large window it rarely fires. Prefer built-in middleware over
  hand-rolled for cross-cutting concerns.
- **Middleware is mostly built-in now.** Only `GuardrailMiddleware` is custom (domain topic +
  profanity). Loop-bounding, summarization, PII masking, and retry/fallback are LangChain
  built-ins — see the Architecture list. When adding a cross-cutting concern, check the
  [built-in middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in)
  first.
