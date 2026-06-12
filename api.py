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
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")

# Models
class DownloadRequest(BaseModel):
    url: str

class DownloadResponse(BaseModel):
    status: str
    message: str
    download_url: Optional[str] = None

# Middleware: Verify API Key
def verify_api_key(x_rapidapi_key: str = Header(None)):
    """Verify RapidAPI key"""
    if not x_rapidapi_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    
    if x_rapidapi_key != RAPIDAPI_KEY:
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

@app.post("/download")
async def download_freepik(
    request: DownloadRequest,
    api_key: str = Depends(verify_api_key)
):
    """
    Download a file from Freepik/Magnific
    
    Parameters:
    - url: Direct link to Freepik/Magnific file
    
    Returns:
    - download_url: Direct download link
    - status: success or error
    """
    
    logger.info(f"Download request: {request.url}")
    
    try:
        # Import and call the main download handler
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

# ============================================
# Error Handler
# ============================================

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.exception("Unhandled exception")
    return {
        "status": "error",
        "message": "Internal server error",
        "detail": str(exc)
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
