from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import os
import asyncio
import sys

from app.database import init_db, SessionLocal
from app.config import PORT, HOST
from app.integrations import team, slack, outlook, unified
from app import timeservice, outbound, scheduler, followups, brief, seed_demo
from app.kb import api as kb_api
from app.kb import meetings as meetings_api
from app.kb import workload as workload_api
from app.agent import api as agent_api

async def background_tick_loop():
    try:
        while True:
            await asyncio.sleep(30)
            db = SessionLocal()
            try:
                scheduler.tick(db)
            except Exception as e:
                import logging
                logging.getLogger(__name__).exception(f"Error in background scheduler tick: {e}")
            finally:
                db.close()
    except asyncio.CancelledError:
        pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup database table structure automatically
    init_db()
    
    if "pytest" not in sys.modules:
        # Register time-change callback hook
        def time_change_callback():
            db = SessionLocal()
            try:
                scheduler.tick(db)
            except Exception as e:
                import logging
                logging.getLogger(__name__).exception(f"Error ticking scheduler on time change: {e}")
            finally:
                db.close()
                
        timeservice.on_time_change.append(time_change_callback)
        
        # Start live background tick loop
        task = asyncio.create_task(background_tick_loop())
        
        yield
        
        # Clean shutdown
        task.cancel()
        try:
            await task
        except Exception:
            pass
    else:
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
app.include_router(kb_api.router)
app.include_router(scheduler.router)
app.include_router(followups.router)
app.include_router(brief.router)
app.include_router(agent_api.router)
app.include_router(meetings_api.router)
app.include_router(workload_api.router)
app.include_router(seed_demo.router)

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
