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
| `app.py` | `create_agent` agent + middleware stack (custom guardrail + 4 built-ins) + `InMemorySaver` checkpointer, loads `.env`, 9 tools (+ per-tool LangSmith trace tags), model-backend selection, structured logging |
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
- **Middleware**: 2 custom guardrails — `ProfanityGuardMiddleware` (`before_model`, whole-word profanity, owns `profanity_strikes`, runs first) + `TopicGuardMiddleware` (`before_model`, topic keyword filter), both `jump_to="end"` — a dynamic system prompt (`system_prompt_middleware`, `@dynamic_prompt`), plus 5 built-ins: `ModelCallLimitMiddleware` (loop cap), `SummarizationMiddleware` (context mgmt), `PIIMiddleware` (email redacted in replies only; `apply_to_input=False` so lookups work), `ModelRetryMiddleware` (retry + friendly message), `HumanInTheLoopMiddleware` (consent gate before personal-data tools)
- **State**: `InMemorySaver` checkpointer owns per-`thread_id` `messages` + `profanity_strikes` + HITL interrupts; runner sends only the new message each turn; `/clear` = fresh thread
- **Tools**: 9 tools — `purchase_history_tool`, `recommendation_tool`, `inventory_tool`, `artist_lookup_tool`, `albums_by_artist_tool`, `genre_catalog_tool`, `browse_genre_tool`, `top_sellers_tool`, `store_reference_tool`; all DB access routes through `_retrieve()` (`@traceable(run_type="retriever")`). Each tool object is tagged (category + `reads_pii` metadata) so its `tool` run is filterable in LangSmith
- **System Prompt**: a `ChatPromptTemplate` rendered per turn via `@dynamic_prompt` (keeps "Current date" fresh, traces as `run_type="prompt"`) — store overview + tool list + email-only identification + catalog-tool guidance; full schema served on demand via `store_reference_tool`
- **Deployment**: `docker compose up --build` → llama.cpp + LangGraph server (Postgres/Redis, from generated `Dockerfile`) + `agent-chat-ui` on the `ai-stack` network; needs `LANGSMITH_API_KEY` + `llama.env`
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
- **Prefer built-in middleware** — call-limit, summarization, PII, retry/fallback all ship in `langchain.agents.middleware`; only the two guardrails (`ProfanityGuardMiddleware` + `TopicGuardMiddleware`) stay custom, each a focused class-based `before_model` node-style hook (no built-in does domain topic/profanity).
- **PIIMiddleware gotcha** — it defaults `apply_to_input=True`. Here email is the *functional identifier*, so input redaction would replace it with `[REDACTED_EMAIL]` before tools run and **break every lookup**. Use `apply_to_input=False, apply_to_output=True` (with `strategy="redact"`) to redact emails only in replies.
- **HITL consent = real interrupt/resume.** `HumanInTheLoopMiddleware(interrupt_on={tool: InterruptOnConfig(allowed_decisions=[...], description=<callable>, when=<predicate>)})`. It raises `interrupt(HITLRequest)`; resume with `Command(resume={"decisions":[{"type":"approve"}|{"type":"reject"}]})`, one decision per interrupted tool call, in order. CLI reads `result["__interrupt__"][0].value`. Requires a checkpointer. Use `when` to skip the interrupt when there's nothing to consent to (e.g. no email yet).
- **`create_agent` won't take a `ChatPromptTemplate`** (`system_prompt` is `str | SystemMessage`). To use a template *and* trace it, render it per turn in a `@dynamic_prompt` middleware and drop the `system_prompt=` arg. `ChatPromptTemplate.format()` prefixes "System: " — use `.format_messages(...)[0].content` for the raw text.
- **Enrich LangSmith run types with `@traceable(run_type=...)`** — `retriever` for DB/data access, `parser` for output shaping, `prompt` for prompt rendering. Verify by reading the tree back: `Client().list_runs(project_name=...)` → `{r.run_type}`. Keep them honest (don't mislabel). The LangSmith `Client()` respects `LANGSMITH_ENDPOINT` (this account is **EU**: `https://eu.api.smith.langchain.com`) — a bare script that skips `load_dotenv()` hits the US endpoint and 401s.
- **Self-hosted LangGraph server needs `LANGSMITH_API_KEY`** for its license (separate from tracing). `langgraph dockerfile <path>` generates the server image; `--add-docker-compose` emits the canonical Postgres(`pgvector`)+Redis compose to copy env-var names/healthchecks from. agent-chat-ui ships no Dockerfile — clone+build it; run it in **proxy mode** (`NEXT_PUBLIC_API_URL=.../api` + server-side `LANGGRAPH_API_URL`) to dodge CORS and keep the key off the browser.
