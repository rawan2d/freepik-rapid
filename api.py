from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional
import logging
from datetime import datetime, timezone
from starlette.concurrency import run_in_threadpool
import asyncio
import time

app = FastAPI(
    title="Freepik Downloader API",
    description="Download files from Freepik and Magnific",
    version="1.0.0"
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api")

class DownloadRequest(BaseModel):
    url: str

class DownloadResponse(BaseModel):
    status: str
    message: str
    download_url: Optional[str] = None

# Global slow mode settings
download_lock = asyncio.Lock()
last_download_time = 0
SLOW_MODE_SECONDS = 15

async def wait_for_slow_mode():
    global last_download_time

    async with download_lock:
        now = time.time()
        elapsed = now - last_download_time

        if elapsed < SLOW_MODE_SECONDS:
            wait_time = SLOW_MODE_SECONDS - elapsed
            logger.info(f"Slow mode active. Waiting {wait_time:.2f} seconds before next download.")
            await asyncio.sleep(wait_time)

        last_download_time = time.time()

@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "Freepik Downloader API",
        "version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.get("/api")
async def api_download(url: str):
    logger.info(f"API download request: {url}")

    try:
        await wait_for_slow_mode()

        from main import handle_freepik_download
        download_url = await run_in_threadpool(handle_freepik_download, url)

        if not download_url:
            return {
                "status": "error",
                "message": "Failed to get download URL",
                "download_url": None
            }

        return {
            "status": "success",
            "message": "Download link generated successfully",
            "download_url": download_url
        }

    except Exception as e:
        logger.exception(f"API download error: {str(e)}")
        return {
            "status": "error",
            "message": f"Download failed: {str(e)}",
            "download_url": None
        }

@app.post("/download")
async def download_freepik(request: DownloadRequest):
    logger.info(f"Download request: {request.url}")

    try:
        await wait_for_slow_mode()

        from main import handle_freepik_download
        download_url = await run_in_threadpool(handle_freepik_download, request.url)

        if not download_url:
            return {
                "status": "error",
                "message": "Failed to get download URL",
                "download_url": None
            }

        return {
            "status": "success",
            "message": "Download link generated successfully",
            "download_url": download_url
        }

    except Exception as e:
        logger.exception(f"Download error: {str(e)}")
        return {
            "status": "error",
            "message": f"Download failed: {str(e)}",
            "download_url": None
        }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
