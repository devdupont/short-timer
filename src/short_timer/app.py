from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from short_timer.config import get_settings
from short_timer.routers import auth, workouts

app = FastAPI(title="short-timer", version="0.1.0")

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(workouts.router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def run() -> None:
    import uvicorn

    uvicorn.run("short_timer.app:app", host="0.0.0.0", port=8000, reload=True)
