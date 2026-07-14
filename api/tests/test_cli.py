"""CLI tests.

These exist because the command was unreachable at one point: Typer collapses a
single-command app into a bare command, so `lgapp import-content <path>` parsed
"import-content" as the path. Unit tests on the importer all passed regardless.
"""

from typer.testing import CliRunner

from lgapp.cli import app

runner = CliRunner()


def test_import_content_is_addressable_as_a_subcommand() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "import-content" in result.stdout


def test_import_content_has_its_own_help() -> None:
    result = runner.invoke(app, ["import-content", "--help"])
    assert result.exit_code == 0
    assert "--deck" in result.stdout
    assert "--dry-run" in result.stdout


def test_a_missing_file_is_rejected_before_touching_the_database() -> None:
    result = runner.invoke(app, ["import-content", "nope.csv", "--deck", "a1"])
    assert result.exit_code != 0
    assert "does not exist" in result.output
