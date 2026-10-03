"""Conversation memory: remember recent turns and rewrite follow-ups into full questions.

"What about Texas?" means nothing on its own. If the session has history and the new
question looks like a follow-up, the model rewrites it into a standalone question
("What was revenue in Texas last quarter?") using the previous turns. Everything
downstream (cache key, retrieval, SQL generation) then works on the standalone question,
and the previous turn's SQL is also passed to the SQL prompt as context.
"""

from __future__ import annotations

import re
import threading
from collections import defaultdict, deque
from dataclasses import dataclass

from analytics_copilot.llm import LLM

FOLLOWUP = re.compile(
    r"^\s*(and|what about|how about|now|same|also|only|just|instead|then|break (it|that|this) "
    r"down|split|compare (that|it|this)|show (that|it|those|them)|by\b|per\b|for\b)|"
    r"\b(that|those|them|it|this|these|same|previous|above)\b", re.I)

REWRITE_PROMPT = """Rewrite the user's latest message as a standalone question that can be
understood without the conversation. Keep every filter, metric and time period that still
applies from earlier turns, and replace the parts the user changed. If it is already
standalone, return it unchanged. Reply with only the question.

Conversation:
{history}

Latest message: {question}"""


@dataclass
class Turn:
    question: str
    standalone: str
    sql: str | None
    summary: str


class Memory:
    def __init__(self, max_turns: int = 5):
        self._turns: dict[str, deque[Turn]] = defaultdict(lambda: deque(maxlen=max_turns))
        self._lock = threading.Lock()

    def history(self, session: str) -> list[Turn]:
        with self._lock:
            return list(self._turns.get(session, ()))

    def add(self, session: str, turn: Turn) -> None:
        with self._lock:
            self._turns[session].append(turn)

    def clear(self, session: str) -> None:
        with self._lock:
            self._turns.pop(session, None)


def looks_like_followup(question: str) -> bool:
    return bool(FOLLOWUP.search(question)) or len(question.split()) <= 4


def rewrite(question: str, history: list[Turn], llm: LLM) -> str:
    if not history or not looks_like_followup(question):
        return question
    convo = "\n".join(f"User: {t.standalone}\nAssistant: {t.summary[:300]}" for t in history[-3:])
    resp = llm.complete([{"role": "user", "content": REWRITE_PROMPT.format(
        history=convo, question=question)}], max_tokens=120)
    text = resp.text.strip().strip('"').splitlines()[0].strip() if resp.text.strip() else ""
    return text or question
