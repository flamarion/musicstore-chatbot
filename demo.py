import sys
import uuid

from app import build_agent, status_banner
from langchain_core.messages import HumanMessage


def new_thread_id() -> str:
    """Fresh conversation id — a new thread starts the checkpointer from empty."""
    return uuid.uuid4().hex


def run_config(thread_id: str) -> dict:
    """Invoke config: bind the checkpointer thread + name/tag the LangSmith run."""
    return {
        "configurable": {"thread_id": thread_id},
        "run_name": "musicstore-support",
        "tags": ["musicstore-demo"],
    }


def run_sample_demo(app):
    # Self-contained prompts (each on its own thread, so they don't share memory)
    # that showcase catalog browsing, top sellers, the email-only privacy gate,
    # and an email-verified lookup.
    prompts = [
        "What do you have in Alternative & Punk?",
        "What are your top-selling albums overall?",
        "Show my purchase history. My name is Luis.",
        "Show my purchase history for luisrojas@yahoo.cl",
    ]
    print("Music Store Support Bot Demo")
    print(status_banner())
    print("-" * 32)
    for prompt in prompts:
        print(f"> {prompt}")
        result = app.invoke(
            {"messages": [HumanMessage(content=prompt)]},
            config=run_config(new_thread_id()),
        )
        print(result["messages"][-1].content)
        print()


def run_interactive_demo(app):
    print("Music Store Support Bot Demo")
    print(status_banner())
    print("Type /quit to exit or /clear to reset the conversation.")
    print("Try: 'What's in Alternative & Punk?', 'top sellers', or 'my history for <email>'")

    # The checkpointer keeps per-thread history, so we send only the new message
    # each turn and never carry the transcript client-side.  /clear = new thread.
    thread_id = new_thread_id()
    # profanity_strikes is session-scoped, not conversation-scoped: /clear resets
    # the thread (history) but we carry the strike count forward so an offender
    # can't dodge the 2-strike ban by clearing between swears.
    strikes = 0
    while True:
        try:
            user_input = input("\nYou: ").strip()
        except EOFError:
            break

        if not user_input:
            continue
        if user_input.lower() in {"/quit", "/exit"}:
            print("\nGoodbye!")
            break
        if user_input.lower() == "/clear":
            thread_id = new_thread_id()   # fresh conversation...
            print("Conversation context cleared.")
            continue                      # ...but `strikes` is intentionally kept

        payload = {"messages": [HumanMessage(content=user_input)]}
        if strikes:  # seed the carried count into the (possibly post-/clear) thread
            payload["profanity_strikes"] = strikes
        result = app.invoke(payload, config=run_config(thread_id))
        strikes = result.get("profanity_strikes", strikes)
        print(f"\nAssistant: {result['messages'][-1].content}")

        # Second profanity strike ends the session — actually kick the user out.
        if strikes >= 2:
            print("\n[Disconnected — session ended.]")
            break


if __name__ == "__main__":
    app = build_agent()
    if "--sample" in sys.argv:
        run_sample_demo(app)
    else:
        run_interactive_demo(app)
