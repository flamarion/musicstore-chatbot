# Memory — Music Store Chatbot

## Environment
- **Python venv**: `~/.virtualenvs/langchain`
  - Activate: `source ~/.virtualenvs/langchain/bin/activate`
- **Dependencies**: `requirements.txt` (langchain, langgraph, langsmith, openai, python-dotenv, etc.)
- **Database**: Chinook SQLite DB, auto-downloaded on first run to `chinook.db`
- **LLM Endpoint**: `http://192.168.1.163:8033/v1` (llama.cpp serving `qwen3.6-35b-a3b`)
- **Config**: `.env` file with `LLM_ENDPOINT`, `LLM_MODEL`, LangSmith credentials

## Key Files
| File | Purpose |
|---|---|
| `app.py` | LLM-driven LangGraph agent (agent node → tools → agent loop), loads `.env`, binds tools to ChatOpenAI |
| `support_bot.py` | Database queries (Chinook DB), tool implementations (5 tools) |
| `demo.py` | Sample and interactive demo runners |
| `database_context.md` | Schema and data insights injected into LLM system prompt |
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
```

## Architecture
- **LLM**: `ChatOpenAI` pointing to local llama.cpp endpoint (`qwen3.6-35b-a3b`)
- **Agent Loop**: LangGraph StateGraph with `agent` node (LLM + tools) → `tools` node (execution) → conditional edge back to agent
- **Tools**: 5 tools bound to LLM — `purchase_history_tool`, `recommendation_tool`, `inventory_tool`, `artist_lookup_tool`, `genre_catalog_tool`
- **System Prompt**: Includes database context from `database_context.md`, instructs LLM to extract customer names (defaults to "Luis")
- **Tracing**: LangSmith enabled via `.env` credentials, middleware prints tool calls to stdout

## Lessons Learned
- Dependencies need to be installed in `~/.virtualenvs/langchain`, not project-local venv
- Dummy `OPENAI_API_KEY` required for local llama.cpp endpoints (OpenAI client validation)
- Demo runs successfully with all 3 sample prompts — LLM correctly routes to tools and generates responses
- Middleware tracing enabled (prints `[middleware] tool_name -> args=...` to stdout)
- `.env` loaded via `python-dotenv` at top of `app.py`
