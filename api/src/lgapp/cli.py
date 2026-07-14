"""Operator CLI.

`uv run lgapp import-content <path> --deck <slug>`
"""

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from lgapp.db import dispose_engine, get_sessionmaker
from lgapp.services.importer import ImportResult, ImportValidationError, import_cards

app = typer.Typer(help="Learn German API operator commands.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Root callback.

    Typer collapses a single-command app into a bare command, which would make
    `lgapp import-content <path>` parse "import-content" as the path argument. Declaring
    a callback keeps subcommands addressable by name and leaves room for the next one.
    """


async def _run_import(path: Path, deck: str, name: str | None, dry_run: bool) -> ImportResult:
    try:
        async with get_sessionmaker()() as session:
            result = await import_cards(
                session, path=path, deck_slug=deck, deck_name=name, dry_run=dry_run
            )
            if dry_run:
                # Nothing should reach the database from a dry run, whatever the code above did.
                await session.rollback()
            else:
                await session.commit()
            return result
    finally:
        await dispose_engine()


@app.command("import-content")
def import_content(
    path: Annotated[Path, typer.Argument(help="A .csv or .json file of cards.", exists=True)],
    deck: Annotated[str, typer.Option(help="Deck slug. Created if it does not exist.")],
    name: Annotated[str | None, typer.Option(help="Deck display name (new decks only).")] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Validate and report, writing nothing.")
    ] = False,
) -> None:
    """Load cards into a deck. Safe to re-run: rows upsert on (deck, external_id)."""
    try:
        result = asyncio.run(_run_import(path, deck, name, dry_run))
    except ImportValidationError as exc:
        typer.secho(f"✗ {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    prefix = "would import" if result.dry_run else "imported"
    typer.secho(
        f"✓ {prefix} {result.rows_read} row(s) into '{result.deck_slug}': "
        f"{result.inserted} new, {result.updated} updated",
        fg=typer.colors.GREEN,
    )
    if not result.dry_run and result.is_noop:
        typer.echo("  (content already up to date)")


if __name__ == "__main__":
    app()
