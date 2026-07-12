from httpx import ASGITransport, AsyncClient

import app.main as main_mod
from app.main import create_app


async def _up() -> bool:
    return True


async def _down() -> bool:
    return False


async def test_liveness_always_ok() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


async def test_readiness_ok_when_deps_up(monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "_check_db", _up)
    monkeypatch.setattr(main_mod, "_check_redis", _up)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"db": "ok", "redis": "ok"}}


async def test_readiness_degraded_when_redis_down(monkeypatch) -> None:
    monkeypatch.setattr(main_mod, "_check_db", _up)
    monkeypatch.setattr(main_mod, "_check_redis", _down)

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "checks": {"db": "ok", "redis": "down"}}
