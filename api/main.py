import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import google.cloud.logging
import firebase_admin

from routers import queries, households, health_alerts

logging.basicConfig(level=logging.INFO)
google.cloud.logging.Client().setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    if not firebase_admin._apps:
        firebase_admin.initialize_app()
    logging.info("🐱 Litter API started")
    yield
    # Shutdown
    logging.info("🐱 Litter API stopped")


app = FastAPI(
    title="Cat Litter Monitor API",
    description="Backend API for the Cat Litter Monitor mobile app",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(queries.router, prefix="/api/v1", tags=["queries"])
app.include_router(households.router, prefix="/api/v1", tags=["households"])
app.include_router(health_alerts.router, prefix="/api/v1", tags=["health_alerts"])


@app.get("/health")
async def health():
    return {"status": "ok", "service": "litter-api"}
