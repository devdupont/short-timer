"""App-level wiring: health/readiness reporting."""

from httpx import AsyncClient


async def test_readiness_reports_database_health(client: AsyncClient) -> None:
    """`/api/ready` reports the database as reachable when it is."""
    response = await client.get("/api/ready")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"
