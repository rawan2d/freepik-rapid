from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import Optional
import os
import logging
from datetime import datetime, timezone

app = FastAPI(
    title="Freepik Downloader API",
    description="Download files from Freepik and Magnific",
    version="1.0.0"
)

# Logger setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# RapidAPI Key from environment
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "").strip()

# Models
class DownloadRequest(BaseModel):
    url: str

class DownloadResponse(BaseModel):
    status: str
    message: str
    download_url: Optional[str] = None

# Middleware: Verify API Key
def verify_api_key(
    x_rapidapi_key: Optional[str] = Header(default=None, alias="X-RapidAPI-Key")
):
    """Verify RapidAPI key"""
    logger.info(f"Received API key header: {'YES' if x_rapidapi_key else 'NO'}")

    if not x_rapidapi_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    if not RAPIDAPI_KEY:
        raise HTTPException(status_code=500, detail="Server API key is not configured")

    if x_rapidapi_key.strip() != RAPIDAPI_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return x_rapidapi_key

# ============================================
# PUBLIC ENDPOINTS
# ============================================

@app.get("/")
async def root():
    """API Health Check"""
    return {
        "status": "online",
        "service": "Freepik Downloader API",
        "version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.get("/api")
async def api_download(
    url: str,
    api_key: Optional[str] = Header(default=None, alias="X-RapidAPI-Key")
):
    """
    Download file from Freepik/Magnific - Query parameter version
    """
    logger.info(f"API download request: {url}")

    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    if not RAPIDAPI_KEY:
        raise HTTPException(status_code=500, detail="Server API key is not configured")

    if api_key.strip() != RAPIDAPI_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    try:
        from main import handle_freepik_download

        download_url = handle_freepik_download(url)

        if not download_url:
            return {
                "status": "error",
                "message": "❌ Failed to get download URL",
                "download_url": None
            }

        return {
            "status": "success",
            "message": "✅ Download link generated successfully",
            "download_url": download_url
        }

    except Exception as e:
        logger.exception(f"API download error: {str(e)}")
        return {
            "status": "error",
            "message": f"❌ Download failed: {str(e)}",
            "download_url": None
        }

@app.post("/download")
async def download_freepik(
    request: DownloadRequest,
    api_key: str = Depends(verify_api_key)
):
    """
    Download a file from Freepik/Magnific - JSON Body version
    """
    logger.info(f"Download request: {request.url}")

    try:
        from main import handle_freepik_download

        download_url = handle_freepik_download(request.url)

        if not download_url:
            return {
                "status": "error",
                "message": "❌ Failed to get download URL",
                "download_url": None
            }

        return {
            "status": "success",
            "message": "✅ Download link generated successfully",
            "download_url": download_url
        }

    except Exception as e:
        logger.exception(f"Download error: {str(e)}")
        return {
            "status": "error",
            "message": f"❌ Download failed: {str(e)}",
            "download_url": None
        }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
