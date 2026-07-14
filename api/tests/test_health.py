from httpx import AsyncClient


async def test_healthz_is_ok(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_healthz_echoes_request_id(client: AsyncClient) -> None:
    response = await client.get("/healthz", headers={"x-request-id": "abc-123"})
    assert response.headers["x-request-id"] == "abc-123"


async def test_healthz_assigns_request_id_when_absent(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.headers["x-request-id"]


async def test_unknown_route_returns_problem_json(client: AsyncClient) -> None:
    response = await client.get("/nope")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["status"] == 404
    assert body["instance"] == "/nope"
