"""Importer tests.

The headline case is idempotency: re-running an unchanged file must be a no-op. Content
will be re-imported often and a duplicate-on-reimport bug would be discovered late and be
painful to unpick.
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lgapp.models import Card, Deck, ImportBatch, ImportStatus, ReviewLog
from lgapp.services.importer import (
    CardRow,
    ImportValidationError,
    import_cards,
    parse,
    source_for,
)
from tests.factories import make_card, make_deck, make_user

HEADER = (
    "external_id,german,english,part_of_speech,article,plural,example_de,example_en,tags,position"
)
ROW_HAUS = "haus,Haus,house,noun,das,Häuser,Das Haus ist groß.,The house is big.,home|a1,1"
ROW_FRAU = "frau,Frau,woman,noun,die,Frauen,Die Frau liest.,The woman reads.,people|a1,2"


def write_csv(tmp_path: Path, *rows: str, name: str = "cards.csv") -> Path:
    path = tmp_path / name
    path.write_text("\n".join([HEADER, *rows]) + "\n", encoding="utf-8")
    return path


async def _card_count(session: AsyncSession, deck_id: object) -> int:
    return (
        await session.execute(select(func.count()).select_from(Card).where(Card.deck_id == deck_id))
    ).scalar_one()


class TestParsing:
    def test_parses_a_valid_row(self, tmp_path: Path) -> None:
        rows = parse(source_for(write_csv(tmp_path, ROW_HAUS)))
        assert len(rows) == 1
        assert rows[0].german == "Haus"
        assert rows[0].article == "das"
        assert rows[0].tags == ["home", "a1"]

    def test_empty_cells_become_null_not_empty_string(self, tmp_path: Path) -> None:
        """CSV cannot express NULL; without this the column fills with ""."""
        row = "sein,sein,to be,verb,,,Ich bin müde.,I am tired.,verbs|a1,1"
        parsed = parse(source_for(write_csv(tmp_path, row)))[0]
        assert parsed.article is None
        assert parsed.plural is None

    def test_reports_every_bad_row_at_once(self, tmp_path: Path) -> None:
        """Fixing content one exception per run would be miserable."""
        path = write_csv(
            tmp_path,
            ",Haus,house,noun,das,Häuser,x,y,home,1",  # missing external_id
            "frau,,woman,noun,die,Frauen,x,y,people,2",  # missing german
            "kind,Kind,,noun,das,Kinder,x,y,people,3",  # missing english
        )
        with pytest.raises(ImportValidationError) as exc:
            parse(source_for(path))
        assert len(exc.value.errors) == 3
        assert {e.line for e in exc.value.errors} == {2, 3, 4}

    def test_rejects_a_non_german_article(self, tmp_path: Path) -> None:
        path = write_csv(tmp_path, "haus,Haus,house,noun,le,Häuser,x,y,home,1")
        with pytest.raises(ImportValidationError, match="der/die/das"):
            parse(source_for(path))

    def test_rejects_an_unknown_column(self, tmp_path: Path) -> None:
        """A typo'd header would otherwise silently drop a column."""
        path = tmp_path / "typo.csv"
        path.write_text(f"{HEADER},germann\n{ROW_HAUS},oops\n", encoding="utf-8")
        with pytest.raises(ImportValidationError, match="germann"):
            parse(source_for(path))

    def test_rejects_duplicate_external_ids_naming_both_lines(self, tmp_path: Path) -> None:
        path = write_csv(tmp_path, ROW_HAUS, ROW_HAUS)
        with pytest.raises(ImportValidationError, match="duplicate of line 2"):
            parse(source_for(path))

    def test_line_numbers_account_for_the_header(self, tmp_path: Path) -> None:
        path = write_csv(tmp_path, ROW_HAUS, "frau,,woman,noun,die,Frauen,x,y,people,2")
        with pytest.raises(ImportValidationError) as exc:
            parse(source_for(path))
        assert exc.value.errors[0].line == 3, "second data row is line 3 in the file"

    def test_reads_json(self, tmp_path: Path) -> None:
        path = tmp_path / "cards.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "external_id": "haus",
                        "german": "Haus",
                        "english": "house",
                        "tags": ["home", "a1"],
                    }
                ]
            ),
            encoding="utf-8",
        )
        rows = parse(source_for(path))
        assert rows[0].tags == ["home", "a1"]

    def test_reads_json_wrapped_in_an_object(self, tmp_path: Path) -> None:
        path = tmp_path / "cards.json"
        path.write_text(
            json.dumps({"cards": [{"external_id": "haus", "german": "Haus", "english": "house"}]}),
            encoding="utf-8",
        )
        assert len(parse(source_for(path))) == 1

    def test_rejects_an_unsupported_format(self, tmp_path: Path) -> None:
        path = tmp_path / "cards.xlsx"
        path.write_text("nope", encoding="utf-8")
        with pytest.raises(ValueError, match="unsupported content format"):
            source_for(path)

    def test_handles_a_utf8_bom(self, tmp_path: Path) -> None:
        """Excel writes a BOM; without utf-8-sig the first header becomes "﻿external_id"."""
        path = tmp_path / "bom.csv"
        path.write_text("﻿" + HEADER + "\n" + ROW_HAUS + "\n", encoding="utf-8")
        assert parse(source_for(path))[0].external_id == "haus"

    def test_whitespace_is_stripped(self) -> None:
        row = CardRow.model_validate(
            {"external_id": "  haus  ", "german": " Haus ", "english": " house "}
        )
        assert row.external_id == "haus"
        assert row.german == "Haus"


class TestImport:
    async def test_creates_the_deck_and_cards(self, session: AsyncSession, tmp_path: Path) -> None:
        result = await import_cards(
            session, path=write_csv(tmp_path, ROW_HAUS, ROW_FRAU), deck_slug="a1", deck_name="A1"
        )
        assert (result.rows_read, result.inserted, result.updated) == (2, 2, 0)

        deck = (await session.execute(select(Deck).where(Deck.slug == "a1"))).scalar_one()
        assert deck.name == "A1"
        assert await _card_count(session, deck.id) == 2

    async def test_reimporting_an_unchanged_file_is_a_noop(
        self, session: AsyncSession, tmp_path: Path
    ) -> None:
        """The property that makes content re-importable.

        "No-op" means genuinely nothing changed — not merely that nothing duplicated.
        """
        path = write_csv(tmp_path, ROW_HAUS, ROW_FRAU)
        await import_cards(session, path=path, deck_slug="a1")
        second = await import_cards(session, path=path, deck_slug="a1")

        assert (second.inserted, second.updated, second.unchanged) == (0, 0, 2)
        assert second.is_noop

        deck = (await session.execute(select(Deck).where(Deck.slug == "a1"))).scalar_one()
        assert await _card_count(session, deck.id) == 2, "re-import must not duplicate"

    async def test_reimport_does_not_rewrite_unchanged_rows(
        self, session: AsyncSession, tmp_path: Path
    ) -> None:
        """An unchanged row must not be touched at all, so updated_at stays meaningful."""
        path = write_csv(tmp_path, ROW_HAUS)
        await import_cards(session, path=path, deck_slug="a1")
        card = (await session.execute(select(Card).where(Card.external_id == "haus"))).scalar_one()
        first_written = card.updated_at

        await import_cards(session, path=path, deck_slug="a1")
        await session.refresh(card)
        assert card.updated_at == first_written

    async def test_only_the_changed_row_is_counted(
        self, session: AsyncSession, tmp_path: Path
    ) -> None:
        await import_cards(session, path=write_csv(tmp_path, ROW_HAUS, ROW_FRAU), deck_slug="a1")
        edited_frau = "frau,Frau,lady,noun,die,Frauen,Die Frau liest.,The woman reads.,people|a1,2"
        result = await import_cards(
            session, path=write_csv(tmp_path, ROW_HAUS, edited_frau), deck_slug="a1"
        )
        assert (result.inserted, result.updated, result.unchanged) == (0, 1, 1)

    async def test_reimport_updates_changed_content_in_place(
        self, session: AsyncSession, tmp_path: Path
    ) -> None:
        await import_cards(session, path=write_csv(tmp_path, ROW_HAUS), deck_slug="a1")
        changed = "haus,Haus,dwelling,noun,das,Häuser,Neu.,New.,home|a1,1"
        result = await import_cards(session, path=write_csv(tmp_path, changed), deck_slug="a1")

        assert (result.inserted, result.updated) == (0, 1)
        card = (await session.execute(select(Card).where(Card.external_id == "haus"))).scalar_one()
        assert card.english == "dwelling"

    async def test_import_never_touches_user_progress(
        self, session: AsyncSession, tmp_path: Path
    ) -> None:
        """The reason content and user state are separate tables.

        A learner's history must survive any number of content re-imports.
        """
        import uuid
        from datetime import UTC, datetime

        from lgapp.models import Rating

        deck = await make_deck(session, slug="a1")
        card = await make_card(session, deck, external_id="haus", german="Haus", english="house")
        user = await make_user(session)
        session.add(
            ReviewLog(
                id=uuid.uuid4(),
                user_id=user.id,
                card_id=card.id,
                rating=Rating.good,
                reviewed_at=datetime.now(UTC),
            )
        )
        await session.flush()

        await import_cards(session, path=write_csv(tmp_path, ROW_HAUS, ROW_FRAU), deck_slug="a1")

        logs = (
            (await session.execute(select(ReviewLog).where(ReviewLog.user_id == user.id)))
            .scalars()
            .all()
        )
        assert len(logs) == 1, "review history must survive a re-import"
        assert logs[0].card_id == card.id, "the card must keep its identity, not be replaced"

    async def test_records_an_audit_row(self, session: AsyncSession, tmp_path: Path) -> None:
        path = write_csv(tmp_path, ROW_HAUS)
        await import_cards(session, path=path, deck_slug="a1")

        batch = (await session.execute(select(ImportBatch))).scalar_one()
        assert batch.status is ImportStatus.succeeded
        assert batch.rows_read == 1
        assert batch.rows_inserted == 1
        assert batch.finished_at is not None
        assert len(batch.checksum) == 64

    async def test_checksum_identifies_an_unchanged_file(
        self, session: AsyncSession, tmp_path: Path
    ) -> None:
        path = write_csv(tmp_path, ROW_HAUS)
        await import_cards(session, path=path, deck_slug="a1")
        await import_cards(session, path=path, deck_slug="a1")

        checksums = (await session.execute(select(ImportBatch.checksum))).scalars().all()
        assert checksums[0] == checksums[1]

    async def test_a_bad_row_writes_nothing(self, session: AsyncSession, tmp_path: Path) -> None:
        """Validation happens before any write, so a broken file cannot half-apply."""
        path = write_csv(tmp_path, ROW_HAUS, "frau,,woman,noun,die,Frauen,x,y,people,2")
        with pytest.raises(ImportValidationError):
            await import_cards(session, path=path, deck_slug="a1")

        assert (
            await session.execute(select(Deck).where(Deck.slug == "a1"))
        ).scalar_one_or_none() is None
        assert (await session.execute(select(ImportBatch))).scalar_one_or_none() is None

    async def test_dry_run_writes_nothing_but_reports_the_diff(
        self, session: AsyncSession, tmp_path: Path
    ) -> None:
        path = write_csv(tmp_path, ROW_HAUS, ROW_FRAU)
        result = await import_cards(session, path=path, deck_slug="a1", dry_run=True)

        assert result.dry_run is True
        assert (result.rows_read, result.inserted, result.updated) == (2, 2, 0)
        assert (
            await session.execute(select(Deck).where(Deck.slug == "a1"))
        ).scalar_one_or_none() is None
        assert (await session.execute(select(ImportBatch))).scalar_one_or_none() is None

    async def test_dry_run_distinguishes_new_changed_and_unchanged(
        self, session: AsyncSession, tmp_path: Path
    ) -> None:
        await import_cards(session, path=write_csv(tmp_path, ROW_HAUS, ROW_FRAU), deck_slug="a1")
        edited_frau = "frau,Frau,lady,noun,die,Frauen,Die Frau liest.,The woman reads.,people|a1,2"
        new_kind = "kind,Kind,child,noun,das,Kinder,Das Kind spielt.,The child plays.,people|a1,3"

        result = await import_cards(
            session,
            path=write_csv(tmp_path, ROW_HAUS, edited_frau, new_kind),
            deck_slug="a1",
            dry_run=True,
        )
        # haus unchanged, frau edited, kind new.
        assert (result.inserted, result.updated, result.unchanged) == (1, 1, 1)

    async def test_decks_do_not_share_an_external_id_namespace(
        self, session: AsyncSession, tmp_path: Path
    ) -> None:
        path = write_csv(tmp_path, ROW_HAUS)
        await import_cards(session, path=path, deck_slug="a1")
        await import_cards(session, path=path, deck_slug="a2")

        cards = (
            (await session.execute(select(Card).where(Card.external_id == "haus"))).scalars().all()
        )
        assert len(cards) == 2, "the same external_id in two decks is two distinct cards"


class TestSeedDeck:
    """The seed deck goes through the same importer real content will."""

    @property
    def path(self) -> Path:
        return Path(__file__).resolve().parents[2] / "seed" / "a1-basics.csv"

    def test_seed_file_is_valid(self) -> None:
        rows = parse(source_for(self.path))
        assert len(rows) == 50
        assert all(row.german and row.english for row in rows)

    async def test_seed_deck_imports_and_is_idempotent(self, session: AsyncSession) -> None:
        first = await import_cards(
            session, path=self.path, deck_slug="a1-basics", deck_name="A1 Basics"
        )
        assert (first.inserted, first.updated) == (50, 0)

        second = await import_cards(session, path=self.path, deck_slug="a1-basics")
        assert (second.inserted, second.updated, second.unchanged) == (0, 0, 50)
        assert second.is_noop

        deck = (await session.execute(select(Deck).where(Deck.slug == "a1-basics"))).scalar_one()
        assert await _card_count(session, deck.id) == 50

    async def test_seed_nouns_have_articles(self, session: AsyncSession) -> None:
        rows = parse(source_for(self.path))
        nouns = [r for r in rows if r.part_of_speech == "noun"]
        assert nouns, "expected some nouns"
        assert all(r.article in {"der", "die", "das"} for r in nouns)
