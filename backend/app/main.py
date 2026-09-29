from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.auth.router import router as auth_router
from app.chat.router import router as chat_router
from app.config import get_settings
from app.db import engine
from app.ingestion.router import router as documents_router
from app.retrieval.router import router as search_router
from app.workspaces.router import router as workspaces_router

# Built frontend (copied here by the Dockerfile). Absent during backend-only dev.
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="Multi-Workspace Document Assistant")
app.include_router(auth_router)
app.include_router(workspaces_router)
app.include_router(documents_router)
app.include_router(search_router)
app.include_router(chat_router)


@app.get("/healthz")
async def healthz() -> JSONResponse:
    settings = get_settings()
    # Only whether each integration is configured — never any part of a value.
    configured = {
        "gemini": settings.gemini_api_key is not None,
        "groq": settings.groq_api_key is not None,
        "discord": settings.discord_webhook_url is not None,
    }
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        # Don't leak connection details; just report the dependency as down.
        return JSONResponse(
            {"status": "degraded", "db": "unreachable", "configured": configured},
            status_code=503,
        )
    return JSONResponse({"status": "ok", "db": "ok", "configured": configured})


if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> Response:
        # Client-side routing: any non-API path returns the SPA shell.
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        candidate = (STATIC_DIR / full_path).resolve()
        if full_path and candidate.is_file() and STATIC_DIR in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
