# Music Store Support Bot Demo

A lightweight demo showing how open-source LangChain and LangGraph components can be combined with LangSmith for a realistic customer-support experience in a music store.

**The story**: a music retailer wants a support bot that can answer two questions: what should I listen to next, and what have I bought before?

## What the bot can do

- **Music recommendations** based on a customer's prior purchases
- **Purchase-history and invoice lookups** for a given customer
- **Inventory snapshot** of the store's catalog
- **Artist search** by keyword or style
- **Genre catalog** summary of the store's music categories

## Architecture

- **LLM**: `ChatOpenAI` pointing to a local llama.cpp endpoint (e.g., `qwen3.6-35b-a3b`)
- **LangChain** — tool abstractions for customer lookup, recommendations, and catalog queries
- **LangGraph** — agent loop with an LLM node that decides tool usage, a tool execution node, and conditional edges to route back to the agent
- **LangSmith** — observability, tracing, and debugging for the agent's decisions
- **Chinook Database** — SQLite backing store with customer, invoice, track, and genre data

## Quick Start

```bash
# 1. Activate the shared environment
source ~/.virtualenvs/langchain/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure your LLM endpoint
# Copy .env.example to .env and set your LLM_ENDPOINT and LLM_MODEL
cp .env.example .env  # if available, or edit .env directly

# 4. Run the demo
python demo.py --sample        # batch mode
python demo.py                 # interactive mode
```

## Project Files

| File | Purpose |
|---|---|
| [app.py](app.py) | LLM-driven LangGraph agent (agent → tools → agent loop), tool binding, system prompt |
| [support_bot.py](support_bot.py) | Chinook database queries and 5 tool implementations |
| [demo.py](demo.py) | Sample and interactive demo runners |
| [database_context.md](database_context.md) | Schema and data insights injected into the LLM system prompt |
| [.env](.env) | LLM endpoint, model name, LangSmith credentials |

## LangSmith Setup

```bash
export LANGSMITH_API_KEY=your-key
export LANGCHAIN_PROJECT=musicstore-support-demo
export LANGSMITH_TRACING=true
```

## More Details

- **Environment & commands**: see [MEMORY.md](MEMORY.md)
- **Current state & roadmap**: see [PROJECT_STATUS.md](PROJECT_STATUS.md)
