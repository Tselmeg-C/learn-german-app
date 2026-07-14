"""Content ingestion.

This is the seam real content data plugs into. Three properties matter:

- **Idempotent.** Cards key on `(deck_id, external_id)`, so re-running an import updates
  in place rather than duplicating. Running the same file twice is a no-op.
- **Non-destructive.** Only content tables are written. A learner's progress is never
  touched by an import, however wrong the file is.
- **All errors at once.** A file with 40 bad rows reports 40 problems, not the first one.
  Fixing content one exception per run is miserable.
"""

import csv
import hashlib
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from lgapp.models import Card, Deck, ImportBatch, ImportStatus

GERMAN_ARTICLES = {"der", "die", "das"}


class CardRow(BaseModel):
    """One row of source content, validated.

    Unknown columns are rejected rather than ignored: a typo'd header would otherwise
    silently drop a column and nobody would notice until the data looked wrong.
    """

    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    external_id: str = Field(min_length=1, max_length=128)
    german: str = Field(min_length=1)
    english: str = Field(min_length=1)
    part_of_speech: str | None = Field(default=None, max_length=32)
    article: str | None = Field(default=None, max_length=8)
    plural: str | None = None
    example_de: str | None = None
    example_en: str | None = None
    tags: list[str] = Field(default_factory=list)
    position: int = 0

    @field_validator(
        "part_of_speech", "article", "plural", "example_de", "example_en", mode="before"
    )
    @classmethod
    def _empty_string_is_null(cls, value: Any) -> Any:
        """CSV has no concept of NULL — every absent cell arrives as "".

        Without this the database would fill up with empty strings that are neither
        absent nor meaningful.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("article")
    @classmethod
    def _article_must_be_german(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalised = value.strip().lower()
        if normalised not in GERMAN_ARTICLES:
            raise ValueError(f"article must be one of der/die/das, got {value!r}")
        return normalised

    @field_validator("tags", mode="before")
    @classmethod
    def _split_tags(cls, value: Any) -> Any:
        """Tags arrive as "food|noun" from CSV and as a real list from JSON."""
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [tag.strip() for tag in value.split("|") if tag.strip()]
        return value


@dataclass(frozen=True, slots=True)
class RowError:
    line: int
    field: str
    message: str

    def __str__(self) -> str:
        return f"line {self.line}: {self.field}: {self.message}"


class ImportValidationError(Exception):
    """Raised with every problem found, not just the first."""

    def __init__(self, errors: Sequence[RowError]) -> None:
        self.errors = list(errors)
        preview = "\n".join(f"  {e}" for e in self.errors[:20])
        more = f"\n  ... and {len(self.errors) - 20} more" if len(self.errors) > 20 else ""
        super().__init__(f"{len(self.errors)} invalid row(s):\n{preview}{more}")


@dataclass(frozen=True, slots=True)
class ImportResult:
    deck_slug: str
    rows_read: int
    inserted: int
    updated: int
    unchanged: int
    dry_run: bool

    @property
    def is_noop(self) -> bool:
        return self.inserted == 0 and self.updated == 0


class ContentSource(Protocol):
    """A source of card rows. Implement this to ingest a new format."""

    def rows(self) -> Iterator[tuple[int, dict[str, Any]]]:
        """Yield (line_number, raw_row). Line numbers are for error messages."""
        ...


class CsvSource:
    def __init__(self, path: Path) -> None:
        self.path = path

    def rows(self) -> Iterator[tuple[int, dict[str, Any]]]:
        with self.path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            for index, row in enumerate(reader, start=2):  # line 1 is the header
                # A short row yields None values; treat them as absent, not as null data.
                yield index, {k: v for k, v in row.items() if k is not None}


class JsonSource:
    def __init__(self, path: Path) -> None:
        self.path = path

    def rows(self) -> Iterator[tuple[int, dict[str, Any]]]:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("cards", [])
        if not isinstance(payload, list):
            raise ValueError('JSON content must be a list of cards, or {"cards": [...]}')
        yield from enumerate(payload, start=1)


def source_for(path: Path) -> ContentSource:
    match path.suffix.lower():
        case ".csv":
            return CsvSource(path)
        case ".json":
            return JsonSource(path)
        case other:
            raise ValueError(f"unsupported content format {other!r}; expected .csv or .json")


def checksum_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse(source: ContentSource) -> list[CardRow]:
    """Validate every row, then raise once with all failures."""
    parsed: list[CardRow] = []
    errors: list[RowError] = []
    seen: dict[str, int] = {}

    for line, raw in source.rows():
        try:
            row = CardRow.model_validate(raw)
        except ValidationError as exc:
            errors.extend(
                RowError(
                    line=line,
                    field=".".join(str(p) for p in error["loc"]) or "row",
                    message=error["msg"],
                )
                for error in exc.errors()
            )
            continue

        # Caught here rather than by the database, so the report names both lines.
        if (first := seen.get(row.external_id)) is not None:
            errors.append(
                RowError(
                    line=line,
                    field="external_id",
                    message=f"duplicate of line {first} ({row.external_id!r})",
                )
            )
            continue
        seen[row.external_id] = line
        parsed.append(row)

    if errors:
        raise ImportValidationError(errors)
    return parsed


# The columns an import owns. Everything else on `cards` is either identity or
# bookkeeping, and must not be compared or overwritten.
CONTENT_COLUMNS = (
    "german",
    "english",
    "part_of_speech",
    "article",
    "plural",
    "example_de",
    "example_en",
    "tags",
    "position",
)


def _differs(existing: Card, row: CardRow) -> bool:
    return any(getattr(existing, column) != getattr(row, column) for column in CONTENT_COLUMNS)


@dataclass(frozen=True, slots=True)
class _Diff:
    to_insert: list[CardRow]
    to_update: list[CardRow]
    unchanged: int


async def _plan(session: AsyncSession, deck: Deck | None, rows: Sequence[CardRow]) -> _Diff:
    """Work out what the file would change. Shared by dry runs and real imports."""
    existing: dict[str, Card] = {}
    if deck is not None:
        result = await session.execute(select(Card).where(Card.deck_id == deck.id))
        existing = {card.external_id: card for card in result.scalars()}

    to_insert, to_update, unchanged = [], [], 0
    for row in rows:
        current = existing.get(row.external_id)
        if current is None:
            to_insert.append(row)
        elif _differs(current, row):
            to_update.append(row)
        else:
            unchanged += 1
    return _Diff(to_insert=to_insert, to_update=to_update, unchanged=unchanged)


async def import_cards(
    session: AsyncSession,
    *,
    path: Path,
    deck_slug: str,
    deck_name: str | None = None,
    dry_run: bool = False,
) -> ImportResult:
    """Load a content file into a deck.

    Validates everything before writing anything: a file with one bad row changes
    nothing. On success the run is recorded in `import_batches`.

    `updated` counts rows whose content actually changed, not rows that happened to
    already exist — so re-running an unchanged file reports 0 inserted and 0 updated, and
    writes nothing.
    """
    rows = parse(source_for(path))
    started = datetime.now(UTC)

    deck = (await session.execute(select(Deck).where(Deck.slug == deck_slug))).scalar_one_or_none()
    diff = await _plan(session, deck, rows)

    if dry_run:
        return ImportResult(
            deck_slug=deck_slug,
            rows_read=len(rows),
            inserted=len(diff.to_insert),
            updated=len(diff.to_update),
            unchanged=diff.unchanged,
            dry_run=True,
        )

    if deck is None:
        deck = Deck(slug=deck_slug, name=deck_name or deck_slug)
        session.add(deck)
        await session.flush()

    batch = ImportBatch(
        deck_id=deck.id,
        source=str(path),
        checksum=checksum_of(path),
        status=ImportStatus.running,
        started_at=started,
        rows_read=len(rows),
    )
    session.add(batch)
    await session.flush()

    changed = diff.to_insert + diff.to_update
    if changed:
        # One statement for the whole file. Unchanged rows are left out entirely rather
        # than rewritten with identical values, which keeps updated_at meaningful and
        # avoids churning dead tuples on a large re-import.
        payload = [{"deck_id": deck.id, **row.model_dump()} for row in changed]
        statement = insert(Card).values(payload)
        await session.execute(
            statement.on_conflict_do_update(
                constraint="uq_cards_deck_id_external_id",
                set_={column: statement.excluded[column] for column in CONTENT_COLUMNS},
            )
        )

    batch.rows_inserted = len(diff.to_insert)
    batch.rows_updated = len(diff.to_update)
    batch.status = ImportStatus.succeeded
    batch.finished_at = datetime.now(UTC)
    await session.flush()

    return ImportResult(
        deck_slug=deck_slug,
        rows_read=len(rows),
        inserted=len(diff.to_insert),
        updated=len(diff.to_update),
        unchanged=diff.unchanged,
        dry_run=False,
    )
