"""LBCA Monitoring System — FastAPI application entry point."""
from __future__ import annotations

import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routers import ai, academic, auth, dashboard, pace, staff, students
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


for router in (
    auth.router,
    staff.router,
    academic.router,
    students.router,
    pace.router,
    dashboard.router,
    ai.router,
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
