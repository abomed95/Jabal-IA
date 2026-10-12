from fastapi import FastAPI

from app.core.errors import install_error_handlers
from app.modules.auth.router import router as auth_router


def create_app() -> FastAPI:
    app = FastAPI(title="Jabal IA API", version="0.1.0")
    install_error_handlers(app)
    app.include_router(auth_router)

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
