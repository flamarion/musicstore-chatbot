# CLAUDE.md — agent context for this repo

Context for AI coding tools (Claude Code and, by convention, other agents) working in this
project. Keep it short; link out for detail.

## What this is

A small **LangChain + LangGraph + LangSmith** demo: a customer-support chatbot for a digital
music store (the Chinook sample DB). Built as a take-home technical task, so favor
**clarity and pragmatism over ceremony** — this is a demo, not production. No formal specs;
keep the file set small (reuse existing docs rather than adding new ones).

## Architecture (one breath)

Built with LangChain `create_agent` (prebuilt ReAct loop, agent ⇄ tools) + a middleware stack (2 custom guardrails + a dynamic system prompt + 5 built-in) + an `InMemorySaver` checkpointer — **no hand-rolled `StateGraph`**:
- `ProfanityGuardMiddleware.before_model` (**custom**) — stateful 2-strike profanity easter egg; owns the `profanity_strikes` state field; runs first so a ban trumps everything; `jump_to="end"` before any LLM call.
- `TopicGuardMiddleware.before_model` (**custom**) — stateless keyword topic classifier; `jump_to="end"` on blatant off-topic input, before any LLM call.
- `system_prompt_middleware` (`@dynamic_prompt`) — renders `SYSTEM_PROMPT_TEMPLATE` (a `ChatPromptTemplate`) each turn so "Current date" stays fresh; wrapped `@traceable(run_type="prompt")` so it shows as a **prompt** run. Supplied here **instead of** `create_agent`'s `system_prompt=` arg.
- `ModelCallLimitMiddleware` (built-in) — `run_limit=8`, `exit_behavior="end"`; bounds the ReAct loop.
- `SummarizationMiddleware` (built-in) — condenses history past `("tokens", 8000)`, keeps recent 20; idiomatic context mgmt (rarely fires).
- `PIIMiddleware("email", strategy="redact", apply_to_input=False, apply_to_output=True)` (built-in) — redacts emails in **replies only**; input stays intact so lookups work. **Do not enable `apply_to_input`** — it would redact the email before tools see it and break the identity gate.
- `ModelRetryMiddleware(on_failure=_endpoint_down_message)` (built-in) — retry with backoff, then friendly message (replaced the old custom `EndpointFallbackMiddleware`).
- `HumanInTheLoopMiddleware` (built-in, `_pii_consent_middleware()`) — **consent gate**: interrupts before `purchase_history_tool` / `recommendation_tool` for approve/reject the **first time** an email is used. The `when` predicate (`_consent_required`) skips email-less calls and any email already approved earlier in the thread (scans history for a successful PII ToolMessage), so consent is asked **once per email per thread**, not every turn; catalog tools auto-approve. Needs the checkpointer to persist the pause; resume with `Command(resume={"decisions":[{"type":"approve"|"reject"}]})`.
- `InMemorySaver` checkpointer — persists per-`thread_id` state (`messages` + `profanity_strikes`) **and** the HITL interrupt; callers send only the new turn.
- 9 tools (8 lookups + `store_reference_tool`) run against the Chinook SQLite DB via one `@traceable(run_type="retriever")` `_retrieve()` helper (retriever runs); the richest formatter is `@traceable(run_type="parser")`. Catalog lookups include `albums_by_artist_tool` (albums for a named artist), `browse_genre_tool` and `top_sellers_tool`. Each tool object carries a category tag (`catalog`/`account`/`reference`) + `reads_pii` metadata (derived from the HITL consent set) so its `tool` run is filterable/groupable in LangSmith without drilling into traces.
- Model backend is **hosted Claude or a local endpoint**, chosen by `resolve_provider()` (`LLM_PROVIDER` / auto-detect from `ANTHROPIC_API_KEY`).

Full diagram and component table: [README.md](README.md#architecture).

## Key files

| File | Purpose |
|---|---|
| [app.py](app.py) | `create_agent` + middleware stack (guardrails, dynamic prompt, PII, retry, HITL consent), `InMemorySaver`, 9 tools (+ per-tool LangSmith trace tags), model-backend selection, `ChatPromptTemplate` system prompt |
| [support_bot.py](support_bot.py) | Chinook DB access via `_retrieve()` (traced `retriever`) + tool implementations, catalog queries, email-only identity gate (`resolve_customer_for_pii`) |
| [demo.py](demo.py) | Sample (`--sample`) and interactive runners; `resolve_consent()` handles the HITL interrupt/resume |
| [langgraph.json](langgraph.json) | LangGraph Server/Studio config → `app.py:make_graph` (deploy the graph via `langgraph dev` / `langgraph up`) |
| [docker-compose.yml](docker-compose.yml) · [Dockerfile](Dockerfile) · [Dockerfile.chat-ui](Dockerfile.chat-ui) | Full-stack deploy: agent server (Postgres/Redis) + agent-chat-ui on the `ai-stack` network; runs on a laptop, model comes from `.env` (remote `LLM_ENDPOINT` / `ANTHROPIC_API_KEY`) — no bundled model |
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
- **Scope control is two layers.** `TopicGuardMiddleware` is a cheap keyword pre-filter that
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
- **Middleware is mostly built-in now.** Only the two guardrails (`ProfanityGuardMiddleware` +
  `TopicGuardMiddleware`) are custom, and only because no built-in does domain topic/profanity —
  each is a standard class-based `before_model` node-style hook. Loop-bounding, summarization,
  PII redaction, retry/fallback, **and the consent gate** (`HumanInTheLoopMiddleware`) are
  LangChain built-ins — see the Architecture list. When adding a cross-cutting concern, check the
  [built-in middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in)
  first.
- **HITL consent is real interrupt/resume, not a prompt trick.** `_pii_consent_middleware()`
  interrupts the graph before a personal-data tool runs; the CLI (`demo.py:resolve_consent`) reads
  `result["__interrupt__"][0].value` (an `HITLRequest`), asks y/N, and resumes with
  `Command(resume={"decisions":[{"type":"approve"|"reject"}]})`. `agent-chat-ui` renders the same
  interrupt as an approve/reject card. Requires a checkpointer (both CLI and served paths have one).
  Consent is **remembered per email per thread**: `_consent_required` scans history for a prior
  successful PII ToolMessage with the same email and skips the interrupt if found — so an approved
  email isn't re-prompted every turn, but a rejection or a different email re-asks.
- **Trace run types are deliberate.** DB access is `@traceable(run_type="retriever")` (`_retrieve`),
  response shaping is `run_type="parser"`, and the system prompt is `run_type="prompt"` (rendered
  from a `ChatPromptTemplate` via `@dynamic_prompt`). Keep run types honest — don't tag a
  non-retrieval as a retriever just to decorate the trace.
- **Deploy the whole thing with `docker compose up --build`** (see README "Full stack with a chat
  UI"): `langgraph-api` (from the generated `Dockerfile`) + Postgres + Redis + `agent-chat-ui`, all
  on the external `ai-stack` network. Runs on a laptop — **no bundled model**; the containerized
  agent reads `LLM_ENDPOINT` (remote OpenAI-compatible host) / `ANTHROPIC_API_KEY` straight from
  `.env` (compose no longer overrides them). The server needs `LANGSMITH_API_KEY` in `.env` (license).
