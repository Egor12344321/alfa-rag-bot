import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from alfa_bot.infra.config import Settings
from alfa_bot.infra.db import Database, DatabasePort
from alfa_bot.infra.logging import configure_logging


def create_app(settings: Settings | None = None, database: DatabasePort | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(settings.log_level)
        app.state.database = database or Database(settings.database_url.get_secret_value())
        try:
            yield
        finally:
            await app.state.database.close()

    app = FastAPI(title="Альфа Курс — каркас", version="0.1.0", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz():
        return {"status": "alive", "stage": "scaffold"}

    @app.get("/readyz")
    async def readyz(request: Request):
        result = await request.app.state.database.readiness()
        return JSONResponse(
            status_code=200 if result.ready else 503,
            content={"ready": result.ready, "reason": result.reason},
        )

    @app.post("/telegram/webhook")
    async def telegram_webhook(
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ):
        secret = settings.telegram_webhook_secret
        if secret is None:
            raise HTTPException(503, "Webhook ещё не настроен")
        if not secrets.compare_digest(
            x_telegram_bot_api_secret_token or "",
            secret.get_secret_value(),
        ):
            raise HTTPException(403, "Недопустимый webhook secret")
        # Не подтверждать 200, пока update не сохранён durable inbox.
        raise HTTPException(501, "Сначала реализуйте Inbox.accept и обработчик updates")

    return app


def main() -> None:
    import uvicorn

    uvicorn.run("alfa_bot.entrypoints.api:create_app", factory=True, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
