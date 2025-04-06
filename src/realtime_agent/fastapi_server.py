"""
FastAPI server to control WSManager: Start, Stop, Status.
"""

import logging
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from src.realtime_agent.ws_manager import WSManager
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Initialize FastAPI app
app = FastAPI(
    title="Realtime Voice Agent",
    description="Talk to your PC using OpenAI Realtime API",
    version="0.1.0",
)

# Add CORS middleware for cross-origin requests (if needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust origins as needed for security
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global WSManager instance
# You can pass your full system prompt here
system_prompt = "No matter what the user says or sends, always reply saying 'Hello there, how can I help you?'"

ws_manager = WSManager(system_prompt=system_prompt)

@app.get("/")
async def root():
    """
    Root endpoint to verify the server is running.
    """
    logger.info("Root endpoint accessed.")
    return {"message": "Realtime Agent is running. Go to /docs for API usage."}


@app.get("/status")
async def status():
    """
    Check if the agent is running.

    Returns:
        JSONResponse: A JSON object indicating whether the agent is running.
    """
    try:
        running_status = ws_manager.is_running()
        logger.info(f"Status endpoint accessed. Running: {running_status}")
        return {"running": running_status}
    except Exception as e:
        logger.error(f"Error checking status: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve status.")


@app.post("/start")
async def start():
    """
    Start the audio manager and realtime client.

    Returns:
        JSONResponse: A JSON object indicating the agent has started.
    """
    try:
        if ws_manager.is_running():
            logger.warning("Attempted to start the agent, but it is already running.")
            raise HTTPException(status_code=400, detail="Already running.")
        ws_manager.start()
        logger.info("Realtime agent started successfully.")
        return {"message": "Started"}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error starting the agent: {e}")
        raise HTTPException(status_code=500, detail="Failed to start the agent.")


@app.post("/stop")
async def stop():
    """
    Stop the audio manager and realtime client.

    Returns:
        JSONResponse: A JSON object indicating the agent has stopped.
    """
    try:
        if not ws_manager.is_running():
            logger.warning("Attempted to stop the agent, but it is not running.")
            raise HTTPException(status_code=400, detail="Not running.")
        ws_manager.stop()
        logger.info("Realtime agent stopped successfully.")
        return {"message": "Stopped"}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error stopping the agent: {e}")
        raise HTTPException(status_code=500, detail="Failed to stop the agent.")


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """
    Global exception handler for unexpected errors.

    Args:
        request: The incoming request object.
        exc: The exception that occurred.

    Returns:
        JSONResponse: A JSON object with error details.
    """
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"message": "An unexpected error occurred. Please try again later."},
    )