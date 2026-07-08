import sys
import uuid

from app import build_agent, status_banner
from langchain_core.messages import HumanMessage
from langgraph.types import Command


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


def resolve_consent(app, result: dict, config: dict, ask) -> dict:
    """Drive the HITL consent gate to completion.

    When the agent is about to run a personal-data tool it interrupts (see
    ``HumanInTheLoopMiddleware`` in app.py); the paused state surfaces as
    ``result["__interrupt__"]``.  We collect an approve/reject decision for each
    pending action and resume with ``Command(resume=...)``, looping until the run
    finishes (a turn can pause more than once).  ``ask(action) -> bool`` decides
    approve (True) or reject (False).
    """
    while result.get("__interrupt__"):
        request = result["__interrupt__"][0].value  # HITLRequest dict
        decisions = [
            {"type": "approve"} if ask(action) else {"type": "reject"}
            for action in request["action_requests"]
        ]
        result = app.invoke(Command(resume={"decisions": decisions}), config=config)
    return result


def _ask_interactive(action: dict) -> bool:
    """Prompt the terminal user to approve/reject a PII lookup."""
    print(f"\n{action.get('description', 'Approve this action?')}")
    return input("Approve? [y/N]: ").strip().lower() in ("y", "yes")


def _ask_auto_approve(action: dict) -> bool:
    """Non-interactive consent for the scripted sample run — always approves,
    but prints the prompt so the consent step is visible in batch output."""
    print(f"\n[consent] {action.get('description', '')}")
    print("[consent] auto-approving for the scripted demo")
    return True


def run_sample_demo(app):
    # Self-contained prompts (each on its own thread, so they don't share memory)
    # that showcase catalog browsing, top sellers, the email-only privacy gate,
    # and an email-verified lookup (which now pauses for consent before it runs).
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
        config = run_config(new_thread_id())
        result = app.invoke(
            {"messages": [HumanMessage(content=prompt)]}, config=config
        )
        result = resolve_consent(app, result, config, _ask_auto_approve)
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
        config = run_config(thread_id)
        result = app.invoke(payload, config=config)
        # The agent may pause here for personal-data consent — resolve it (y/N)
        # before we read the final reply.
        result = resolve_consent(app, result, config, _ask_interactive)
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
