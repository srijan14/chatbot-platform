"""Intent Classifier — POC stub.

In a multi-skill bot this would route the user message to the right skill (or to
a multi-skill orchestration). For the POC the bot has only 'tool_call' enabled,
so this is a constant function. The class exists to prove the architectural slot.
"""


def classify(user_message: str) -> str:
    return "transactional"
