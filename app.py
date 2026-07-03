import os
from functools import wraps
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from support_bot import (
    ensure_chinook_database,
    find_artists_by_keyword,
    get_customer_purchase_history,
    get_inventory_snapshot,
    get_most_common_genres,
    recommend_music_for_customer,
)

# Load environment variables from .env file
load_dotenv()

# Set dummy API key for local LLM endpoints (llama.cpp, Ollama, etc.)
if not os.getenv("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = "sk-not-needed-for-local"


def trace_middleware(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        print(f"[middleware] {fn.__name__} -> args={args}, kwargs={kwargs}")
        result = fn(*args, **kwargs)
        print(f"[middleware] {fn.__name__} completed")
        return result

    return wrapped


@tool
@trace_middleware
def purchase_history_tool(customer_name: str) -> str:
    """Look up a customer's recent invoices and purchase history from the music store database."""
    return get_customer_purchase_history(customer_name)


@tool
@trace_middleware
def recommendation_tool(customer_name: str) -> str:
    """Recommend music genres for a customer based on what they have bought before."""
    return recommend_music_for_customer(customer_name)


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


DATABASE_CONTEXT_PATH = Path(__file__).resolve().parent / "database_context.md"
DATABASE_CONTEXT = DATABASE_CONTEXT_PATH.read_text(encoding="utf-8")

SYSTEM_PROMPT = f"""You are a friendly and helpful customer support assistant for a digital music store.

You have access to the following tools:
- purchase_history_tool: Look up a customer's recent invoices and purchase history
- recommendation_tool: Recommend music genres for a customer based on their purchase history
- inventory_tool: Give a quick inventory snapshot for the music store
- artist_lookup_tool: Search the catalog for artists that match a keyword or style
- genre_catalog_tool: Summarize the most common music genres in the catalog

Here is the database context to help you understand the data:

{DATABASE_CONTEXT}

When a user asks about their purchase history or recommendations, try to extract their name from the conversation.
If no name is provided, politely ask the user for their name (or what they'd like to be called) before looking up any data.

Be conversational, helpful, and concise. Always use tools when appropriate rather than making up information.
If you are unsure which tool to use, ask the user for clarification."""


class State(TypedDict):
    messages: Annotated[list, add_messages]


def configure_langsmith() -> None:
    if os.getenv("LANGSMITH_API_KEY"):
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
        os.environ.setdefault("LANGCHAIN_PROJECT", os.getenv("LANGCHAIN_PROJECT", "musicstore-chatbot"))
        os.environ.setdefault("LANGSMITH_ENDPOINT", os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"))
    else:
        os.environ.setdefault("LANGSMITH_TRACING", "false")
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")


def build_agent():
    ensure_chinook_database()
    configure_langsmith()

    # Initialize LLM from local llama.cpp endpoint
    llm = ChatOpenAI(
        model=os.getenv("LLM_MODEL", "qwen3.6-35b-a3b"),
        base_url=os.getenv("LLM_ENDPOINT", "http://192.168.1.163:8033/v1"),
        temperature=0.3,
    )

    # Bind tools to the LLM
    tools = [purchase_history_tool, recommendation_tool, inventory_tool, artist_lookup_tool, genre_catalog_tool]
    llm_with_tools = llm.bind_tools(tools)

    all_tools = {
        "purchase_history_tool": purchase_history_tool,
        "recommendation_tool": recommendation_tool,
        "inventory_tool": inventory_tool,
        "artist_lookup_tool": artist_lookup_tool,
        "genre_catalog_tool": genre_catalog_tool,
    }

    def agent_node(state: State):
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
        response = llm_with_tools.invoke(messages)
        return {"messages": [response]}

    def tool_node(state: State):
        tool_messages = []
        for tool_call in state["messages"][-1].tool_calls:
            tool_name = tool_call["name"]
            args = tool_call["args"]
            tool = all_tools.get(tool_name)
            if tool:
                result = tool.invoke(args)
                tool_messages.append(
                    ToolMessage(content=result, tool_call_id=tool_call["id"], name=tool_name)
                )
            else:
                tool_messages.append(
                    ToolMessage(
                        content=f"Unknown tool: {tool_name}",
                        tool_call_id=tool_call["id"],
                        name=tool_name,
                    )
                )
        return {"messages": tool_messages}

    def should_continue(state: State):
        messages = state["messages"]
        last_message = messages[-1]
        if isinstance(last_message, AIMessage) and last_message.tool_calls:
            return "tools"
        return END

    workflow = StateGraph(State)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)
    workflow.add_edge("tools", "agent")
    workflow.set_entry_point("agent")
    workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})

    return workflow.compile()


if __name__ == "__main__":
    app = build_agent()
    for prompt in ["Recommend music for Luis", "Show my invoice history for Luis"]:
        print(f"> {prompt}")
        output = app.invoke({"messages": [HumanMessage(content=prompt)]})
        print(output["messages"][-1].content)
        print()
