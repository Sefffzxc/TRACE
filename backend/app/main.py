"""LBCA Monitoring System — FastAPI application entry point."""
from __future__ import annotations

import logging
import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routers import ai, academic, auth, dashboard, pace, schools, staff, students
from app.core.config import settings
from app.db.session import init_models

# The schema check is a diagnostic, not a feature. If the module is missing
# — a half-copied deployment, most likely — the API must still start: an
# aid to debugging that can itself take the service down is worse than no
# aid at all. A stub keeps every reference below valid.
try:
    from app.db.schema_check import STATE as SCHEMA_STATE
    from app.db.schema_check import verify as verify_schema
except ImportError:  # pragma: no cover - only on an incomplete deployment
    SCHEMA_STATE = {"checked": False, "ok": True}

    async def verify_schema():
        logging.getLogger("lbca").warning(
            "app/db/schema_check.py is missing, so the database schema was "
            "not verified at startup. Copy it into place to get a clear "
            "report when the models and the database disagree."
        )
        return SCHEMA_STATE


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_models()
    # create_all adds missing tables but never adds a column to a table
    # that already exists, so a deployed database can be one migration
    # behind the models and every query mentioning a new column fails as a
    # 500. This logs exactly which columns are missing, and the command
    # that fixes it, instead of leaving it to be guessed from the browser.
    #
    # Wrapped because startup must not depend on it: a database that is
    # briefly unreachable is a different problem, and turning it into a
    # boot loop would hide it behind a service that never comes up.
    try:
        await verify_schema()
    except Exception:  # noqa: BLE001 - diagnostics never block startup
        logging.getLogger("lbca").exception("the schema check itself failed")

    # Say out loud which origins the browser will be allowed to call from.
    # A CORS rejection looks identical to a dead API from the console, so
    # the one list that decides it should not have to be inferred.
    origins = settings.cors_origins
    log = logging.getLogger("lbca.cors")
    if not origins:
        log.error("ALLOWED_ORIGINS is empty — every browser request will be "
                  "refused. Set it to the front end's URL.")
    elif "*" in origins:
        # A wildcard cannot be combined with credentials; browsers reject
        # the pair outright, which presents as a blanket CORS failure.
        log.error("ALLOWED_ORIGINS contains '*', which browsers refuse to "
                  "accept alongside credentials. Name the front end's URL "
                  "instead.")
    else:
        log.info("CORS allows: %s", ", ".join(origins))
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "Student PACE monitoring and early-warning API for "
        f"{settings.SCHOOL_NAME}."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

class CatchUnhandledErrors:
    """Turn a crash into a JSON 500 *inside* the CORS middleware.

    ``@app.exception_handler(Exception)`` does not do this: Starlette hands
    that handler to ServerErrorMiddleware, which is the outermost layer and
    sits ABOVE CORS, so its response leaves without an
    Access-Control-Allow-Origin header and the browser reports a CORS error
    instead of the 500. This middleware is added before CORSMiddleware, which
    makes it the inner of the two, so CORS headers are applied to its reply.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def send_wrapper(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:  # noqa: BLE001 - reported to the client
            if started:
                raise
            response = await unhandled_handler(Request(scope), exc)
            await response(scope, receive, send)


# Order matters: middleware added later wraps middleware added earlier, so
# this must come before CORSMiddleware to sit inside it.
app.add_middleware(CatchUnhandledErrors)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

uploads = pathlib.Path(settings.UPLOAD_DIR)
uploads.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(uploads)), name="uploads")


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError):
    """Turn Pydantic's error list into one readable sentence for the UI."""
    messages = []
    for error in exc.errors():
        field = ".".join(str(part) for part in error["loc"][1:]) or "request"
        message = error["msg"].replace("Value error, ", "")
        messages.append(f"{field}: {message}")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": " · ".join(messages)},
    )


async def unhandled_handler(request: Request, exc: Exception):
    """Return a real response for a crash instead of letting it escape.

    Called from ``CatchUnhandledErrors``, which runs inside the CORS
    middleware, so the browser can read the response and show what actually
    went wrong instead of reporting a CORS failure. The message is
    logged in full and only its type is sent to the client, because a
    database error's text can carry table and column names.
    """
    logging.getLogger("lbca").exception(
        "unhandled error on %s %s", request.method, request.url.path
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": (
                "The server hit an unexpected error. The details are in the "
                "service log."
            ),
            "error_type": type(exc).__name__,
            "path": request.url.path,
        },
    )


for router in (
    auth.router,
    staff.router,
    academic.router,
    students.router,
    pace.router,
    dashboard.router,
    ai.router,
    schools.router,
):
    app.include_router(router)


@app.get("/", tags=["Meta"])
async def root():
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "school": settings.SCHOOL_NAME,
        "docs": "/docs",
    }


@app.get("/health", tags=["Meta"])
async def health():
    """Liveness, plus whether the database matches the models.

    The schema state is here so a mismatch can be read from a browser
    without shell access to the service. It does not change the status:
    the application is up, it is the data layer that is behind.
    """
    body = {
        "status": "ok",
        # The exact origins a browser may call from. Not a secret — any
        # browser can discover it — and it is the fastest way to tell a
        # CORS rejection apart from a dead API, which look the same from
        # the console.
        "allowed_origins": settings.cors_origins,
    }
    if SCHEMA_STATE.get("checked") and not SCHEMA_STATE.get("ok"):
        body["schema"] = {
            "up_to_date": False,
            "missing_tables": SCHEMA_STATE.get("missing_tables", []),
            "missing_columns": SCHEMA_STATE.get("missing_columns", {}),
            "fix": "run: python add_missing_columns.py --apply",
            "why": (
                "create_all adds missing tables but never adds a column to a "
                "table that already exists, so redeploying does not clear this."
            ),
        }
    return body