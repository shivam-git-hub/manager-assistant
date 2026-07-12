from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import os

from app.database import init_db
from app.config import PORT, HOST
from app.integrations import team, slack, outlook, unified
from app import timeservice, outbound

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup database table structure automatically
    init_db()
    yield

app = FastAPI(
    title="Manager Assistant Integration Portal",
    description="Backend services for tracking team status across Slack, Outlook, and Dashboard.",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(timeservice.router)
app.include_router(team.router)
app.include_router(slack.router)
app.include_router(outlook.router)
app.include_router(unified.router)
app.include_router(outbound.router)

# Ensure static files directory exists
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)

# Mount static files to serve the Simulator UI
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": f"Internal Server Error: {str(exc)}"}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=True)
