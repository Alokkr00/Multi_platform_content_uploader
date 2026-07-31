"""
server.py - FastAPI Application for Content Uploader Bot

Serves the web dashboard and includes modular APIRouters for accounts, sources, posts, and system management.
"""

import os
import sys
import queue
import secrets
import logging
import threading
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

import db
from scheduler import scheduler

# Import modular APIRouters
from routes.accounts import router as accounts_router
from routes.sources import router as sources_router
from routes.posts import router as posts_router
from routes.system import router as system_router

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("clipflow.server")


# ── Dashboard Auth Secret Setup ───────────────────────────────────────

def get_or_create_dashboard_secret() -> str:
    """Retrieve DASHBOARD_SECRET from environment or auto-generate a secure token."""
    secret = os.getenv("DASHBOARD_SECRET", "").strip()
    if not secret:
        secret = secrets.token_hex(16)
        os.environ["DASHBOARD_SECRET"] = secret
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        try:
            with open(env_path, "a", encoding="utf-8") as f:
                f.write(f"\nDASHBOARD_SECRET={secret}\n")
            logger.info("Generated new DASHBOARD_SECRET and saved to .env")
        except Exception as ex:
            logger.warning(f"Could not persist DASHBOARD_SECRET to .env: {ex}")
    return secret

DASHBOARD_SECRET = get_or_create_dashboard_secret()

log_queue = queue.Queue()

def _async_log_writer():
    """Background thread that writes logs to the database from the queue."""
    while True:
        try:
            item = log_queue.get()
            if item is None:
                break
            levelname, message = item
            db.add_log(levelname, message)
            log_queue.task_done()
        except Exception:
            pass

writer_thread = threading.Thread(target=_async_log_writer, daemon=True)
writer_thread.start()


class DBLogHandler(logging.Handler):
    def emit(self, record):
        try:
            log_queue.put((record.levelname, record.getMessage()))
        except Exception:
            pass

logger_obj = logging.getLogger("clipflow")
if not any(isinstance(h, DBLogHandler) for h in logger_obj.handlers):
    db_handler = DBLogHandler()
    db_handler.setLevel(logging.INFO)
    logger_obj.addHandler(db_handler)


# ── Application Lifespan ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    """Lifespan context manager for database initialization and background scheduler."""
    logger.info("Initializing database...")
    db.init_db()
    logger.info("Database initialized")

    db.reset_stuck_post_states()
    logger.info("Stuck post states reset successfully")

    await scheduler.start()
    logger.info("Scheduler started")

    yield

    logger.info("Shutting down scheduler...")
    await scheduler.stop()
    logger.info("Application shutdown complete")


# ── FastAPI App Setup & Middleware ────────────────────────────────────

app = FastAPI(
    title="ClipFlow Engine",
    description="ClipFlow — Multi-Platform Social Media Distribution Engine with approval queue & transcoding",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_and_auth_middleware(request: Request, call_next):
    """
    Global security middleware that enforces Bearer token authentication
    on all /api/* routes except public dashboard assets.
    """
    path = request.url.path

    if path.startswith("/api/"):
        auth_header = request.headers.get("Authorization", "")
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()

        if token != DASHBOARD_SECRET:
            logger.warning(f"Unauthorized API request attempt to '{path}'")
            return JSONResponse(
                {"detail": "Unauthorized. Invalid or missing Bearer token."},
                status_code=401
            )

    response = await call_next(request)
    return response


# Static & Upload files mounts
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

UPLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")


@app.get("/", include_in_schema=False)
async def serve_dashboard():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse({"error": "Dashboard not found. Place index.html in static/"}, status_code=404)


# ── Register APIRouters ───────────────────────────────────────────────

app.include_router(accounts_router)
app.include_router(sources_router)
app.include_router(posts_router)
app.include_router(system_router)


# ── Entry Point ───────────────────────────────────────────────────────

if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))

    print(f"\n  ClipFlow starting...")
    print(f"  Dashboard: http://localhost:{port}")
    print(f"  API Docs:  http://localhost:{port}/docs\n")

    uvicorn.run(
        "server:app",
        host=host,
        port=port,
        reload=False,
        log_level="info"
    )
