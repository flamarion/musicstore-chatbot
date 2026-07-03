import sys

from app import build_agent
from langchain_core.messages import HumanMessage


def run_sample_demo(app):
    prompts = [
        "Recommend music for Luis",
        "Show my invoice history for Luis",
        "What is the current inventory?",
    ]
    print("Music Store Support Bot Demo")
    print("-" * 32)
    for prompt in prompts:
        print(f"> {prompt}")
        result = app.invoke({"messages": [HumanMessage(content=prompt)]})
        print(result["messages"][-1].content)
        print()


def run_interactive_demo(app):
    print("Music Store Support Bot Demo")
    print("Type /quit to exit or /clear to reset the conversation.")
    print("Try questions like: 'Recommend music for Luis' or 'What is in stock?'")

    conversation = []
    while True:
        try:
            user_input = input("\nYou: ").strip()
        except EOFError:
            break

        if not user_input:
            continue
        if user_input.lower() in {"/quit", "/exit"}:
            break
        if user_input.lower() == "/clear":
            conversation = []
            print("Conversation context cleared.")
            continue

        conversation.append(HumanMessage(content=user_input))
        result = app.invoke({"messages": conversation})
        assistant_reply = result["messages"][-1].content
        conversation = result["messages"]
        print(f"\nAssistant: {assistant_reply}")


if __name__ == "__main__":
    app = build_agent()
    if "--sample" in sys.argv:
        run_sample_demo(app)
    else:
        run_interactive_demo(app)
