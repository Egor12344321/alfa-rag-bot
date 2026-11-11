from fastapi.testclient import TestClient
from pydantic import SecretStr

from alfa_bot.entrypoints.api import create_app
from alfa_bot.infra.config import Settings
from alfa_bot.infra.db import Readiness


class FakeDatabase:
    def __init__(self):
        self.closed = False

    async def readiness(self):
        return Readiness(False, "knowledge_index_missing")

    async def close(self):
        self.closed = True


def test_alive_does_not_imply_knowledge_ready():
    database = FakeDatabase()
    with TestClient(create_app(Settings(_env_file=None), database)) as client:
        assert client.get("/healthz").status_code == 200
        response = client.get("/readyz")
        assert response.status_code == 503
        assert response.json()["reason"] == "knowledge_index_missing"
    assert database.closed


def test_webhook_checks_secret_and_never_acknowledges_unsaved_update():
    settings = Settings(_env_file=None, telegram_webhook_secret=SecretStr("test-only-secret"))
    with TestClient(create_app(settings, FakeDatabase())) as client:
        assert client.post("/telegram/webhook", json={"update_id": 1}).status_code == 403
        response = client.post(
            "/telegram/webhook",
            json={"update_id": 1},
            headers={"X-Telegram-Bot-Api-Secret-Token": "test-only-secret"},
        )
        assert response.status_code == 501
