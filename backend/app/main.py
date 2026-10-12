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


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_models()
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


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    """Return a real response for a crash instead of letting it escape.

    An exception that propagates past the application is turned into a plain
    500 by Starlette's outermost error middleware, which sits ABOVE the CORS
    middleware — so the browser never sees an Access-Control-Allow-Origin
    header and reports a CORS failure. The developer then goes looking at
    the CORS configuration, which was never the problem.

    Handling it here keeps the response inside the CORS middleware, so the
    browser can read it and show what actually went wrong. The message is
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
    return {"status": "ok"}