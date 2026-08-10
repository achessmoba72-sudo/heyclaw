from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from heyclaw_shared.settings import get_settings

from app import __version__
from app.api.router import api_router
from app.core.logging import configure_logging


@asynccontextmanager
async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
    configure_logging(get_settings())
    yield


def create_app() -> FastAPI:
    application = FastAPI(
        title="HeyClaw Voice Backend",
        version=__version__,
        lifespan=lifespan,
    )
    application.include_router(api_router)
    return application


app = create_app()
