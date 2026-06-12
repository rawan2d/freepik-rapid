from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import Optional
import os
import logging
import asyncio
from datetime import datetime, timezone
from pathlib import Path

# Import existing modules
from plan_manager import (
    is_plan_active, get_user_plan_info, 
    can_download, increment_download_count, get_remaining_downloads,
    PLAN_DURATION_DAYS, PLAN_DAILY_LIMIT,
    is_admin, add_admin, remove_admin, add_plan_user, remove_plan_user, get_all_plan_users
)

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
ADMIN_KEY = os.getenv("ADMIN_KEY", "")

# Models
class DownloadRequest(BaseModel):
    url: str
    user_id: int

class DownloadResponse(BaseModel):
    status: str
    message: str
    download_url: Optional[str] = None
    remaining: Optional[int] = None

class StatusResponse(BaseModel):
    user_id: int
    can_download: bool
    remaining_downloads: Optional[int]
    plan_active: bool
    plan_info: Optional[dict] = None

class PlanResponse(BaseModel):
    status: str
    message: str

# Middleware: Verify API Key
def verify_api_key(x_rapidapi_key: str = Header(None)):
    """Verify RapidAPI key"""
    if not x_rapidapi_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    
    if x_rapidapi_key != RAPIDAPI_KEY and x_rapidapi_key != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    return x_rapidapi_key

# ============================================
# PUBLIC ENDPOINTS (User Commands)
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

@app.post("/download", response_model=DownloadResponse)
async def download_freepik(
    request: DownloadRequest,
    api_key: str = Depends(verify_api_key)
):
    """
    Download a file from Freepik/Magnific
    
    Parameters:
    - url: Direct link to Freepik/Magnific file
    - user_id: Your user ID
    
    Returns:
    - download_url: Direct download link (may be temporary)
    - remaining: Downloads left today
    """
    user_id = request.user_id
    
    logger.info(f"Download request from user {user_id}: {request.url}")
    
    # Check if user has active plan
    if not is_plan_active(user_id):
        return DownloadResponse(
            status="error",
            message="❌ No active plan. Contact admin for a plan.",
            remaining=0
        )
    
    # Check daily limit
    if not can_download(user_id):
        remaining = get_remaining_downloads(user_id)
        return DownloadResponse(
            status="error",
            message=f"❌ Daily download limit reached. Try again tomorrow.",
            remaining=remaining or 0
        )
    
    try:
        # Import and call the main download handler
        from main import handle_freepik_download_api
        
        download_url = await handle_freepik_download_api(request.url, user_id)
        
        # Increment counter only on success
        increment_download_count(user_id)
        remaining = get_remaining_downloads(user_id)
        
        return DownloadResponse(
            status="success",
            message="✅ Download link generated successfully",
            download_url=download_url,
            remaining=remaining
        )
    
    except Exception as e:
        logger.exception(f"Download error for user {user_id}")
        return DownloadResponse(
            status="error",
            message=f"❌ Download failed: {str(e)}",
            remaining=get_remaining_downloads(user_id)
        )

@app.get("/status/{user_id}", response_model=StatusResponse)
async def check_status(
    user_id: int,
    api_key: str = Depends(verify_api_key)
):
    """
    Check your download status and plan info
    
    Returns:
    - can_download: Whether you can download today
    - remaining_downloads: Downloads left today
    - plan_active: If you have an active plan
    - plan_info: Detailed plan information
    """
    logger.info(f"Status check for user {user_id}")
    
    plan_info = get_user_plan_info(user_id)
    plan_active = is_plan_active(user_id)
    remaining = get_remaining_downloads(user_id) if plan_active else None
    
    return StatusResponse(
        user_id=user_id,
        can_download=can_download(user_id),
        remaining_downloads=remaining,
        plan_active=plan_active,
        plan_info=plan_info
    )

@app.get("/myplan/{user_id}")
async def get_my_plan(
    user_id: int,
    api_key: str = Depends(verify_api_key)
):
    """Get your current plan details"""
    plan_info = get_user_plan_info(user_id)
    
    if not plan_info:
        raise HTTPException(
            status_code=404,
            detail="No active plan found. Contact admin."
        )
    
    return {
        "status": "success",
        "user_id": user_id,
        "plan_info": plan_info
    }

# ============================================
# ADMIN ENDPOINTS (Admin Only)
# ============================================

def verify_admin_key(x_rapidapi_key: str = Header(None)):
    """Verify admin API key"""
    if x_rapidapi_key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Not authorized as admin")
    return x_rapidapi_key

@app.post("/admin/add-plan/{user_id}", response_model=PlanResponse)
async def admin_add_plan(
    user_id: int,
    duration_days: int = PLAN_DURATION_DAYS,
    api_key: str = Depends(verify_admin_key)
):
    """
    [ADMIN ONLY] Add a plan to a user
    
    Parameters:
    - user_id: Target user ID
    - duration_days: Plan duration in days (default: 5)
    """
    try:
        add_plan_user(user_id, duration_days)
        logger.info(f"Admin added {duration_days}-day plan for user {user_id}")
        
        return PlanResponse(
            status="success",
            message=f"✅ Plan added for user {user_id} ({duration_days} days, {PLAN_DAILY_LIMIT} downloads/day)"
        )
    except Exception as e:
        logger.exception(f"Error adding plan for user {user_id}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/admin/remove-plan/{user_id}", response_model=PlanResponse)
async def admin_remove_plan(
    user_id: int,
    api_key: str = Depends(verify_admin_key)
):
    """
    [ADMIN ONLY] Remove plan from a user
    """
    try:
        remove_plan_user(user_id)
        logger.info(f"Admin removed plan for user {user_id}")
        
        return PlanResponse(
            status="success",
            message=f"✅ Plan removed for user {user_id}"
        )
    except Exception as e:
        logger.exception(f"Error removing plan for user {user_id}")
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/admin/all-plans")
async def admin_get_all_plans(api_key: str = Depends(verify_admin_key)):
    """[ADMIN ONLY] List all active plans"""
    try:
        plan_users = get_all_plan_users()
        
        if not plan_users:
            return {
                "status": "success",
                "total_users": 0,
                "users": []
            }
        
        users_info = []
        for item in plan_users:
            user_id = item["user_id"]
            plan_info = item["plan_info"]
            
            try:
                end_date = datetime.fromisoformat(plan_info.get("end_date"))
                remaining_days = (end_date - datetime.now(timezone.utc)).days + 1
            except:
                remaining_days = 0
            
            users_info.append({
                "user_id": user_id,
                "downloads_today": plan_info.get("daily_downloads", 0),
                "daily_limit": plan_info.get("daily_limit", PLAN_DAILY_LIMIT),
                "days_remaining": remaining_days,
                "plan_expires": plan_info.get("end_date")
            })
        
        return {
            "status": "success",
            "total_users": len(users_info),
            "users": users_info
        }
    except Exception as e:
        logger.exception("Error fetching all plans")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/admin/add-admin/{user_id}", response_model=PlanResponse)
async def admin_add_admin(
    user_id: int,
    api_key: str = Depends(verify_admin_key)
):
    """[ADMIN ONLY] Add a user as admin"""
    try:
        add_admin(user_id)
        return PlanResponse(
            status="success",
            message=f"✅ User {user_id} is now an admin"
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/admin/remove-admin/{user_id}", response_model=PlanResponse)
async def admin_remove_admin(
    user_id: int,
    api_key: str = Depends(verify_admin_key)
):
    """[ADMIN ONLY] Remove admin access from user"""
    try:
        remove_admin(user_id)
        return PlanResponse(
            status="success",
            message=f"✅ Admin access removed for user {user_id}"
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

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
