"""Command-line entry point: `uv run acp --help`."""

from __future__ import annotations

import time

import typer

from analytics_copilot.config import get_settings

app = typer.Typer(no_args_is_help=True, add_completion=False)
db_app = typer.Typer(no_args_is_help=True, help="Warehouse database commands.")
app.add_typer(db_app, name="db")


@db_app.command("seed")
def db_seed(seed: int = typer.Option(42, help="Random seed; same seed, same data.")) -> None:
    """Generate the synthetic retail data and load it (replaces existing rows)."""
    from analytics_copilot.data.generate import generate
    from analytics_copilot.data.load import load

    t0 = time.perf_counter()
    tables = generate(seed)
    t1 = time.perf_counter()
    counts = load(get_settings().dsn(admin=True), tables)
    t2 = time.perf_counter()
    for name, n in counts.items():
        typer.echo(f"{name:<24} {n:>9,} rows")
    typer.echo(f"generated in {t1 - t0:.1f}s, loaded in {t2 - t1:.1f}s")


@app.command("catalog")
def catalog_build() -> None:
    """Refresh configs/catalog_values.json (distinct values of low-cardinality columns)."""
    from analytics_copilot.schema.catalog import build_values

    values = build_values(get_settings().dsn())
    typer.echo(f"wrote values for {len(values)} columns")


@app.command("eval")
def eval_cmd(
    label: str = typer.Option(..., help="Name for this run (evaluation/runs/<label>.jsonl)."),
    split: str = typer.Option("all", help="dev, test or all"),
    model: str = typer.Option(None, help="Gateway model name (default: LLM_MODEL)."),
    no_retrieval: bool = typer.Option(False, help="Put the whole schema in every prompt."),
    max_corrections: int = typer.Option(2, help="Self-correction rounds (0 disables)."),
    ids: str = typer.Option(None, help="Comma-separated question ids to run."),
) -> None:
    """Run the golden question set and print a summary."""
    import json

    from analytics_copilot.eval.runner import run
    from analytics_copilot.llm import GatewayLLM

    summary = run(GatewayLLM(model=model), label=label, split=split,
                  use_retrieval=not no_retrieval, max_corrections=max_corrections,
                  ids=ids.split(",") if ids else None)
    typer.echo(json.dumps(summary, indent=2))


if __name__ == "__main__":
    app()
