# Project Status — Music Store Chatbot

## Current State
- ✅ Dependencies installed and working
- ✅ Demo runs successfully with all 3 sample prompts
- ✅ LLM integration complete — agent uses local llama.cpp endpoint (`qwen3.6-35b-a3b`)
- ✅ LangGraph agent loop working (agent → tools → agent)
- ✅ 5 tools bound to LLM: purchase history, recommendations, inventory, artist lookup, genre catalog
- ✅ Database context injected into system prompt via `database_context.md`
- ✅ Middleware tracing enabled (prints tool calls to stdout)
- ✅ Chinook database auto-downloads on first run
- ✅ LangSmith tracing configured via `.env`

## What's Done
- **LLM-driven agent**: Replaced keyword-based router with LangGraph agent loop using `ChatOpenAI` (local llama.cpp)
- **5 tools**: `purchase_history_tool`, `recommendation_tool`, `inventory_tool`, `artist_lookup_tool`, `genre_catalog_tool`
- **Agent architecture**: `agent_node` (LLM invocation with tools bound) → `tool_node` (execution) → `should_continue` (conditional edge back to agent)
- **Database context**: `database_context.md` provides schema, relationships, and data insights to the LLM
- **Configuration**: `.env` with LLM endpoint, model name, LangSmith credentials; `python-dotenv` loads at startup
- **Demo**: Sample mode (3 prompts) and interactive mode with conversation history
- **System prompt**: Includes database context, instructs LLM to extract customer names (defaults to "Luis")

## Known Issues & Future Improvements

### Bugs / Fragile Logic
- **Customer name extraction** (`support_bot.py:156`) — `user_message.split()[-1]` grabs only the last word. Two-word names (e.g. "Luis Gomez") or trailing words (e.g. "please") break the lookup silently.
- **Name matching ambiguity** (`support_bot.py:41`) — `LIKE '%name%'` with `LIMIT 5`/no uniqueness check. If two customers partially match, the DB returns whichever orders first with no disambiguation.

### Reliability
- **Hardcoded fallback IP** (`app.py:122-123`) — defaults to `192.168.1.163` and model name. Fine for local llama.cpp, but will confuse anyone else running this. No error handling if the endpoint is unreachable.

### Logging / Observability
- **PII in stdout** (`app.py:32`) — `trace_middleware` prints raw `args`/`kwargs` via `print()`. Customer names are PII going to stdout unfiltered. Should use structured logging with a log level, and redact/omit args in production.

### Housekeeping
- **Dead `pytest` dependency** (`requirements.txt`) — `pytest` is listed and `.pytest_cache/` exists, but there are no test files in the repo. Currently dead weight.

### Feature Requests
- **Topic guardrails** — keep the assistant scoped to music store topics (recommendations, purchase history, inventory, artist/genre lookups). Off-topic asks (general knowledge, coding help, unrelated tasks) should be politely declined and redirected back to what the bot can actually help with, instead of being answered or hallucinated.
- **Profanity easter egg** — if the user curses at the bot, respond in-character with a lighthearted warning that one more instance will end the session. On a repeat offense, end the conversation with a playful "the bot hunts you down to teach you a lesson" message. This is a cosmetic/humor feature only — not a real moderation or safety mechanism, and shouldn't be relied on to filter genuinely abusive input.

## What's Next
- Dev to address remaining known issues (name extraction, name-matching ambiguity, hardcoded fallback IP, PII in stdout logging, dead `pytest` dependency)
- Design + implement topic guardrails and the profanity easter egg (see Feature Requests above)

## Recent Changes
- 2026-07-03: Migrated from keyword router to LLM-powered agent with llama.cpp integration
- 2026-07-03: Added 2 new tools (artist lookup, genre catalog) and database context injection
- 2026-07-03: Consolidated session memory into local MEMORY.md and PROJECT_STATUS.md
