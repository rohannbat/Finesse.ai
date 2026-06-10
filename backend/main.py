from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import auth, coach, food, insights, integrations, nutrition, sync
from app.database import Base, engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    # MVP: create tables on startup. Move to Alembic migrations before
    # the schema needs to evolve in place.
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="HealthSync API", version="0.1.0", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(integrations.router)
app.include_router(insights.router)
app.include_router(sync.router)
app.include_router(nutrition.router)
app.include_router(food.router)
app.include_router(coach.router)


@app.get("/healthz", tags=["meta"])
def healthcheck():
    return {"status": "ok"}
