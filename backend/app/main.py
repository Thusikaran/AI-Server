from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import get_db, find_all_cameras
from app.routers import shops, cameras, bulk, models, worker_api, dashboard, trigger_api
from app.workers.motion_worker import MotionWorkerManager
import os
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="AI Monitor Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(shops.router)
app.include_router(cameras.router)
app.include_router(bulk.router)
app.include_router(models.router)
app.include_router(worker_api.router)
app.include_router(dashboard.router)
app.include_router(trigger_api.router)

os.makedirs("/app/uploads", exist_ok=True)
os.makedirs("/app/uploads/snapshots", exist_ok=True)
app.mount("/api/uploads", StaticFiles(directory="/app/uploads"), name="uploads")
app.mount("/api/snapshots", StaticFiles(directory="/app/uploads/snapshots"), name="snapshots")

@app.on_event("startup")
def startup_event():
    from app.utils.settings import get_server_mode
    server_mode = get_server_mode()
    
    if server_mode == "cloud":
        print("Server starting in CLOUD API Mode. Continuous polling workers disabled.")
        return
        
    print("Server starting in STANDALONE Mode. Restoring continuous polling workers...")
    db = get_db()
    try:
        from app.routers.worker_api import motion_manager
        from app.workers.worker_manager import manager
        
        active_cameras = find_all_cameras(filter_active_only=True)
        for cam in active_cameras:
            cid = cam["id"]
            rtsp = cam.get("rtsp_url") or cam.get("source", "")
            
            # Restore motion worker
            if cam.get('motion_active', False):
                motion_manager.start(cid, rtsp, cam.get("shop_id"), cam.get("name"))

            # Restore standard AI model workers (YOLO)
            running_assignments = db["camera_model_assignments"].count_documents({
                "camera_id": cid,
                "is_running": True
            })
            if running_assignments > 0:
                manager.start_worker(cid)
                
        print(f"Restored workers for {len(active_cameras)} active cameras from MongoDB Atlas.")
    except Exception as e:
        print(f"Error restoring workers during startup: {e}")

@app.get("/")
def read_root():
    return {"message": "AI Monitor Server API is running (MongoDB Atlas Connected)"}
