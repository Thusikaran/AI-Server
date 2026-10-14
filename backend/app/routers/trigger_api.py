from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from app.database import get_db, find_all_cameras
import os
import shutil
import uuid

router = APIRouter(
    prefix="/api/trigger",
    tags=["trigger"]
)

active_cloud_workers = {}

@router.post("/")
async def trigger_camera_event(
    shop_name: str = Form(...),
    camera_name: str = Form(...),
    model_name: str = Form("human"),
    clip: UploadFile = File(None),
    db = Depends(get_db)
):
    """
    Endpoint for local Edge PCs to trigger AI detection on AWS.
    If 'clip' is provided, it processes the uploaded video instantly.
    If 'clip' is None, it opens the live RTSP stream for 30 seconds.
    'model_name' allows targeting a specific YOLO model (e.g. human, vehicle).
    """
    shop = db["shops"].find_one({"name": shop_name})
    if not shop:
        raise HTTPException(status_code=404, detail=f"Shop '{shop_name}' not found")

    camera = None
    for c in shop.get("cameras", []):
        if c.get("name") == camera_name:
            camera = c
            break

    if not camera:
        raise HTTPException(status_code=404, detail=f"Camera '{camera_name}' not found in shop '{shop_name}'")

    clip_path = None
    if clip:
        upload_dir = "/app/uploads/clips"
        os.makedirs(upload_dir, exist_ok=True)
        clip_filename = f"trigger_{uuid.uuid4()}_{clip.filename}"
        clip_path = os.path.join(upload_dir, clip_filename)
        
        with open(clip_path, "wb") as buffer:
            shutil.copyfileobj(clip.file, buffer)

    model_mapping = {
        "human": "yolo_human",
        "vehicle": "yolo_vehicle",
        "fire": "yolo_fire",
        "weapon": "yolo_weapon",
        "person": "yolo_human",
        "car": "yolo_vehicle"
    }
    
    target_model = model_mapping.get(model_name.lower(), f"yolo_{model_name.lower()}")
    camera_id = camera.get("camera_id") or camera.get("id")

    # Cleanup dead workers
    dead_keys = [k for k, w in active_cloud_workers.items() if not w.is_alive()]
    for k in dead_keys:
        del active_cloud_workers[k]

    worker_key = (camera_id, target_model)
    if worker_key in active_cloud_workers and active_cloud_workers[worker_key].is_alive():
        active_cloud_workers[worker_key].extend_duration(30)
        return {
            "status": "success",
            "message": f"Trigger extended for {camera_name}. Added 30s to {target_model} pipeline.",
            "clip_uploaded": clip is not None,
            "target_model": target_model
        }

    from app.workers.yolo_event_worker import YoloEventWorker
    worker = YoloEventWorker(camera_id=camera_id, model_type=target_model, clip_path=clip_path, duration_s=45)
    
    worker.daemon = True
    worker.start()
    active_cloud_workers[worker_key] = worker

    return {
        "status": "success",
        "message": f"Trigger received for {camera_name}. AI processing started for model: {target_model}.",
        "clip_uploaded": clip is not None,
        "target_model": target_model
    }
