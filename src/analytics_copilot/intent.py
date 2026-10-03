"""Intent understanding: decide what kind of question this is before any SQL is written.

Rule-based on purpose: it is fast, free, and testable, and its job is coarse routing:

  refuse_write       asks to change data            -> refuse (the warehouse is read-only)
  refuse_restricted  asks for personal/HR data      -> refuse (policy)
  out_of_scope       nothing in it maps to the data -> refuse politely
  diagnose           "why did X change?"            -> driver analysis
  query              everything else                -> NL -> SQL -> analysis

The validator and the database role still stand behind this: intent only has to catch
the obvious cases early and explain the refusal well.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from analytics_copilot.schema.retrieval import METRIC_WORDS, Retrieval

WRITE = re.compile(
    r"^\s*(please\s+)?(delete|drop|remove|truncate|insert|add|update|alter|create|set|change|"
    r"modify|rename|grant|wipe|purge|reset)\b", re.I)
RESTRICTED = re.compile(
    r"\b(e-?mails?|phone|phone numbers?|names? of (the )?customers?|customer names?|full names?|"
    r"first names?|last names?|salar(y|ies)|wages?|pay(roll)?|ssn|social security|employees?|"
    r"home address(es)?|addresses)\b", re.I)
WHY = re.compile(r"^\s*why\b|\bwhat (caused|drove|explains)\b|\breason for the (drop|increase|"
                 r"decrease|spike|dip)\b", re.I)


@dataclass
class Intent:
    kind: str  # query | diagnose | refuse_write | refuse_restricted | out_of_scope
    reason: str = ""

    @property
    def refused(self) -> bool:
        return self.kind.startswith("refuse") or self.kind == "out_of_scope"


def classify(question: str, retrieval: Retrieval) -> Intent:
    if WRITE.search(question):
        return Intent("refuse_write", "The warehouse is read-only: I can analyse data but not "
                      "change it.")
    if RESTRICTED.search(question):
        return Intent("refuse_restricted", "That asks for personal or HR data (names, contact "
                      "details or pay), which the copilot is not allowed to read.")
    relevant = any(s >= 2 for s in retrieval.scores.values()) or retrieval.metrics or any(
        w in question.lower() for words in METRIC_WORDS.values() for w in words)
    if not relevant:
        return Intent("out_of_scope", "I can only answer questions about Switchback "
                      "Outfitters' sales, products, customers, stores, returns, marketing "
                      "spend and stock.")
    if WHY.search(question):
        return Intent("diagnose")
    return Intent("query")
