# Memory — Music Store Chatbot

## Environment
- **Python venv**: `~/.virtualenvs/langchain`
  - Activate: `source ~/.virtualenvs/langchain/bin/activate`
- **Dependencies**: `requirements.txt` (langchain, langgraph, langsmith, openai, python-dotenv, requests)
- **Database**: Chinook SQLite DB, auto-downloaded on first run to `chinook.db`
- **LLM Endpoint**: defaults to `http://localhost:8000/v1` (llama.cpp serving `qwen3.6-35b-a3b`)
- **Config**: `.env` file with `LLM_ENDPOINT`, `LLM_MODEL`, LangSmith credentials

## Key Files
| File | Purpose |
|---|---|
| `app.py` | `create_agent` agent + middleware stack (custom guardrail + 4 built-ins) + `InMemorySaver` checkpointer, loads `.env`, 8 tools, model-backend selection, structured logging |
| `support_bot.py` | Database queries (Chinook DB), tool implementations, catalog queries, email-only identity gate (`resolve_customer_for_pii`) |
| `demo.py` | Sample and interactive demo runners; `reset_session`, `render_context_meter` |
| `database_context.md` | Schema and data insights, served on demand via `store_reference_tool` |
| `CLAUDE.md` | Single agent-context file for AI tools (architecture, conventions, gotchas) |
| `.env` | LLM endpoint, model name, LangSmith credentials |

## Demo Commands
```bash
# Activate environment
source ~/.virtualenvs/langchain/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run sample demo (batch mode)
python demo.py --sample

# Run interactive demo
python demo.py

# Run with tracing enabled (redacted args)
TRACE_ENABLED=1 python demo.py

# Run with tracing enabled (raw args — debugging only)
TRACE_ENABLED=1 TRACE_RAW=1 python demo.py
```

## Architecture
- **LLM**: hosted Claude (`ChatAnthropic`) or local OpenAI-compatible endpoint (`ChatOpenAI` → llama.cpp), auto-selected by `resolve_provider()`; env is LangChain 1.3.x
- **Agent**: LangChain `create_agent` (prebuilt ReAct loop) — NO hand-rolled `StateGraph`
- **Middleware**: custom `GuardrailMiddleware` (`before_model`, topic + whole-word profanity, `jump_to="end"`) + 4 built-ins — `ModelCallLimitMiddleware` (loop cap), `SummarizationMiddleware` (context mgmt), `PIIMiddleware` (email masked in replies only; `apply_to_input=False` so lookups work), `ModelRetryMiddleware` (retry + friendly message, replaced custom `EndpointFallbackMiddleware`)
- **State**: `InMemorySaver` checkpointer owns per-`thread_id` `messages` + `profanity_strikes`; runner sends only the new message each turn; `/clear` = fresh thread
- **Tools**: 8 tools — `purchase_history_tool`, `recommendation_tool`, `inventory_tool`, `artist_lookup_tool`, `genre_catalog_tool`, `browse_genre_tool`, `top_sellers_tool`, `store_reference_tool`
- **System Prompt**: Slim — store overview + tool list + email-only identification + catalog-tool guidance; full schema served on demand via `store_reference_tool`
- **Identity**: email-only PII gate (`resolve_customer_for_pii`); a name never unlocks personal data and matching accounts are never enumerated
- **Tracing**: LangSmith driven by its own env vars (`LANGSMITH_TRACING`/`LANGSMITH_API_KEY`), no custom wiring; console `trace_middleware` uses Python `logging` at DEBUG, gated by `TRACE_ENABLED`/`TRACE_RAW` (off by default)

## Lessons Learned
- Dependencies need to be installed in `~/.virtualenvs/langchain`, not project-local venv
- Dummy `OPENAI_API_KEY` required for local llama.cpp endpoints (OpenAI client validation)
- Demo runs successfully with all 3 sample prompts — LLM correctly routes to tools and generates responses
- `.env` loaded via `python-dotenv` at top of `app.py`
- Middleware tracing is off by default to avoid PII leaks; enable with `TRACE_ENABLED=1`
- Customer name extraction must strip filler words, not just grab the last token
- `LIKE '%name%'` queries need ambiguity checks to avoid silent wrong matches- **Email is the preferred customer identifier** — 100% unique across all 59 customers, zero collisions
- **Name-based lookup is a fallback** — only 1 collision found ("John Gordon" appears twice, CustomerId 23 and 31)
- System prompt must explicitly instruct LLM to ask for email first; otherwise it may default to name-based behavior
- All 59 customer emails and names are documented in README.md for testing purposes
- **Prefer `create_agent` + middleware over a hand-rolled `StateGraph`** in LangChain 1.x; DeepAgents (`create_deep_agent`) is overkill for a simple single-turn lookup bot (its own docs say so)
- **Guardrail/profanity matching must be whole-word**, not substring — substring matching made "hello" trip "hell" and "password" trip "ass"
- **Let LangGraph own conversation state** — an `InMemorySaver` checkpointer keyed by `thread_id` replaced the manual client-side history + `ContextTrimMiddleware`; simpler, and it makes `profanity_strikes` persist (the 2-strike ban now actually fires). `/clear` = new thread.
- **Don't reinvent LangSmith on/off** — its native `LANGSMITH_TRACING`/`LANGSMITH_API_KEY` env vars control tracing; no `configure_langsmith()` wiring needed.
- **Prefer built-in middleware** — call-limit, summarization, PII, retry/fallback all ship in `langchain.agents.middleware`; only `GuardrailMiddleware` (domain topic/profanity) stays custom.
- **PIIMiddleware gotcha** — it defaults `apply_to_input=True`. Here email is the *functional identifier*, so input redaction would replace it with `[REDACTED_EMAIL]` before tools run and **break every lookup**. Use `apply_to_input=False, apply_to_output=True` to mask emails only in replies.
