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


if __name__ == "__main__":
    app()
