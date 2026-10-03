"""Build and execute notebooks/01_evaluation_analysis.ipynb from the saved evaluation runs.

usage: uv run python scripts/build_notebook.py
"""

from __future__ import annotations

from pathlib import Path

import nbformat
from nbclient import NotebookClient

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "notebooks" / "01_evaluation_analysis.ipynb"

CELLS = [
    ("md", """# Evaluation analysis

Reads the saved runs in `evaluation/runs/` (no model or database needed) and looks at where the
copilot succeeds and fails. Each run file has one record per golden question, including the
SQL the model produced."""),
    ("code", """import json, math
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt

RUNS = Path("../evaluation/runs")
def load(label):
    return pd.DataFrame([json.loads(x) for x in (RUNS / f"{label}.jsonl").read_text().splitlines()])

runs = {label: load(label) for label in
        ["all-v4", "all-v4-noretrieval", "all-v4-nocorrect", "all-v4-3b"]}
sql = {k: v[v.kind == "sql"] for k, v in runs.items()}
{k: f"{v.correct.sum()}/{len(v)}" for k, v in sql.items()}"""),
    ("md", """## Accuracy with 95% intervals

With 56 questions, one question is ~1.8 points. The Wilson interval shows how much of each
difference could be noise."""),
    ("code", """def wilson(k, n, z=1.96):
    p = k / n
    c = (p + z*z/(2*n)) / (1 + z*z/n)
    h = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1 + z*z/n)
    return c - h, c + h

names = {"all-v4": "7B full", "all-v4-noretrieval": "7B, no retrieval",
         "all-v4-nocorrect": "7B, no correction", "all-v4-3b": "3B full"}
rows = []
for k, v in sql.items():
    lo, hi = wilson(v.correct.sum(), len(v))
    rows.append((names[k], v.correct.mean(), lo, hi))
df = pd.DataFrame(rows, columns=["config", "accuracy", "lo", "hi"])
fig, ax = plt.subplots(figsize=(7, 2.8))
ax.errorbar(df.accuracy, df.config, xerr=[df.accuracy - df.lo, df.hi - df.accuracy],
            fmt="o", color="#2a78d6", capsize=4)
ax.set_xlim(0.3, 1.0); ax.set_xlabel("execution accuracy (95% Wilson interval)")
ax.invert_yaxis(); ax.grid(axis="x", color="#e6e5e0"); plt.tight_layout()
df.round(3)"""),
    ("md", """## Accuracy by question category"""),
    ("code", """by_cat = pd.DataFrame({names[k]: v.groupby("category").correct.mean() for k, v in sql.items()})
by_cat["n"] = sql["all-v4"].groupby("category").size()
by_cat.round(2)"""),
    ("md", """## Prompt size: what schema retrieval saves"""),
    ("code", """pd.DataFrame({
    "mean prompt tokens": {names[k]: v.prompt_tokens.mean() for k, v in sql.items()},
    "mean tables in prompt": {names[k]: v.tables_selected.mean() for k, v in sql.items()},
}).round(1)"""),
    ("md", """## Does the 3B model fail on the same questions as 7B?"""),
    ("code", """both = sql["all-v4"][["id", "category", "correct"]].merge(
    sql["all-v4-3b"][["id", "correct"]], on="id", suffixes=("_7b", "_3b"))
pd.crosstab(both.correct_7b, both.correct_3b, rownames=["7B correct"], colnames=["3B correct"])"""),
    ("md", """If the smaller model were right on questions the larger one misses, combining them
could help. The cross-tab shows whether the 3B failures are simply a superset."""),
    ("md", """## What the self-correction loop actually changed"""),
    ("code", """full = sql["all-v4"]
corrected = full[full.attempts > 1][["id", "attempts", "correct", "first_attempt_correct", "error"]]
corrected"""),
    ("md", """## Remaining failures of the full pipeline"""),
    ("code", """pd.set_option("display.max_colwidth", 120)
full[~full.correct][["id", "category", "question"]]"""),
]


def main() -> None:
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_markdown_cell(src) if kind == "md" else
                nbformat.v4.new_code_cell(src) for kind, src in CELLS]
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3",
                                 "language": "python"}
    NotebookClient(nb, timeout=120, kernel_name="python3",
                   resources={"metadata": {"path": str(OUT.parent)}}).execute()
    OUT.parent.mkdir(exist_ok=True)
    nbformat.write(nb, OUT)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
