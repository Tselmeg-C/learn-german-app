import json
import logging

from lgapp.logging import JsonFormatter, request_id_var


def _record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord("t", logging.INFO, "f.py", 1, "hello", (), None)
    record.__dict__.update(extra)
    return record


def test_formats_as_json_with_core_fields() -> None:
    payload = json.loads(JsonFormatter().format(_record()))
    assert payload["message"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "t"
    assert "ts" in payload


def test_includes_request_id_when_set() -> None:
    token = request_id_var.set("rid-1")
    try:
        payload = json.loads(JsonFormatter().format(_record()))
    finally:
        request_id_var.reset(token)
    assert payload["request_id"] == "rid-1"


def test_omits_request_id_when_unset() -> None:
    assert "request_id" not in json.loads(JsonFormatter().format(_record()))


def test_promotes_extras_to_top_level() -> None:
    payload = json.loads(JsonFormatter().format(_record(status=200, duration_ms=1.5)))
    assert payload["status"] == 200
    assert payload["duration_ms"] == 1.5


def test_drops_uvicorn_ansi_duplicate() -> None:
    payload = json.loads(JsonFormatter().format(_record(color_message="\x1b[36mhello\x1b[0m")))
    assert "color_message" not in payload
