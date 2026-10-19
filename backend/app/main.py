from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()
    app = FastAPI(title="RoleMatch API", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Content-Type", settings.csrf_header],
    )

    from app.api import routes_auth, routes_resume

    app.include_router(routes_auth.router)
    app.include_router(routes_resume.router)
    for name in ("routes_preferences", "routes_jobs", "routes_sources", "routes_applications", "routes_analytics",
                 "routes_tailoring", "routes_account"):
        try:
            module = __import__(f"app.api.{name}", fromlist=["router"])
        except ModuleNotFoundError as exc:
            if exc.name != f"app.api.{name}":
                raise
            continue
        app.include_router(module.router)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
