from fastapi import APIRouter, Depends, HTTPException, status
from typing import Dict, Any
from datetime import datetime
from app.database import get_db, find_all_cameras, find_camera_by_id, update_camera_by_id
from app.workers.worker_manager import manager
from app.workers.motion_worker import MotionWorkerManager

router = APIRouter(prefix="/api/workers", tags=["Workers"])

motion_manager = MotionWorkerManager()

@router.post("/cameras/{camera_id}/theft/start")
def start_camera_theft(camera_id: int):
    cid = int(camera_id)
    cam = find_camera_by_id(cid)
    if not cam:
        raise HTTPException(status_code=404, detail="Camera not found")
    update_camera_by_id(cid, {"theft_active": True, "updated_at": datetime.utcnow()})
    return {"message": "Cloud Theft AI tracking enabled"}

@router.post("/cameras/{camera_id}/theft/stop")
def stop_camera_theft(camera_id: int):
    cid = int(camera_id)
    cam = find_camera_by_id(cid)
    if cam:
        update_camera_by_id(cid, {"theft_active": False, "updated_at": datetime.utcnow()})
    return {"message": "Cloud Theft AI tracking disabled"}

@router.post("/cameras/{camera_id}/models/{model_id}/start")
def start_model_on_camera(camera_id: int, model_id: int, db = Depends(get_db)):
    cid = int(camera_id)
    mid = int(model_id)

    db["camera_model_assignments"].update_one(
        {"camera_id": cid, "model_id": mid},
        {"$set": {"is_running": True, "started_at": datetime.utcnow()}},
        upsert=True
    )

    cam = find_camera_by_id(cid)
    if cam:
        rtsp = cam.get("rtsp_url") or cam.get("source", "")
        motion_manager.start(cid, rtsp, cam.get("shop_id"), cam.get("name"))
        update_camera_by_id(cid, {"motion_active": True, "updated_at": datetime.utcnow()})

    manager.start_worker(cid)
    return {"message": "Model and motion started on camera"}

@router.post("/cameras/{camera_id}/models/{model_id}/stop")
def stop_model_on_camera(camera_id: int, model_id: int, db = Depends(get_db)):
    cid = int(camera_id)
    mid = int(model_id)

    db["camera_model_assignments"].update_one(
        {"camera_id": cid, "model_id": mid},
        {"$set": {"is_running": False}}
    )

    remaining = db["camera_model_assignments"].count_documents({
        "camera_id": cid,
        "is_running": True
    })
    if remaining == 0:
        manager.stop_worker(cid)

    return {"message": "Model stopped on camera"}

@router.post("/cameras/{camera_id}/motion/start")
def start_motion_worker(camera_id: int):
    cid = int(camera_id)
    cam = find_camera_by_id(cid)
    if not cam:
        raise HTTPException(status_code=404, detail="Camera not found")
    
    update_camera_by_id(cid, {"motion_active": True, "updated_at": datetime.utcnow()})
    rtsp = cam.get("rtsp_url") or cam.get("source", "")
    motion_manager.start(cid, rtsp, cam.get("shop_id"), cam.get("name"))
    return {"message": f"Motion worker started for camera {cid}"}

@router.post("/cameras/{camera_id}/motion/stop")
def stop_motion_worker(camera_id: int, db = Depends(get_db)):
    cid = int(camera_id)
    running_assignments = db["camera_model_assignments"].count_documents({
        "camera_id": cid,
        "is_running": True
    })
    
    if running_assignments > 0:
        raise HTTPException(
            status_code=400, 
            detail="Cannot disable motion while AI models are running. Please disable the models first."
        )

    cam = find_camera_by_id(cid)
    if cam:
        update_camera_by_id(cid, {"motion_active": False, "updated_at": datetime.utcnow()})

    motion_manager.stop(cid)
    return {"message": f"Motion worker stopped for camera {cid}"}

@router.post("/shops/{shop_id}/models/{model_id}/start")
def start_model_on_shop(shop_id: int, model_id: int, db = Depends(get_db)):
    sid = int(shop_id)
    mid = int(model_id)
    shop = db["shops"].find_one({"$or": [{"shop_id": sid}, {"id": sid}]})
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    for cam in shop.get("cameras", []):
        cid = cam.get("camera_id") or cam.get("id")
        db["camera_model_assignments"].update_one(
            {"camera_id": cid, "model_id": mid},
            {"$set": {"is_running": True, "started_at": datetime.utcnow()}},
            upsert=True
        )
        rtsp = cam.get("rtsp_url") or cam.get("source", "")
        motion_manager.start(cid, rtsp, sid, cam.get("name"))
        update_camera_by_id(cid, {"motion_active": True, "updated_at": datetime.utcnow()})
        manager.start_worker(cid)

    return {"message": f"Model and motion started on all cameras in shop {sid}"}

@router.post("/shops/{shop_id}/models/{model_id}/stop")
def stop_model_on_shop(shop_id: int, model_id: int, db = Depends(get_db)):
    sid = int(shop_id)
    mid = int(model_id)
    shop = db["shops"].find_one({"$or": [{"shop_id": sid}, {"id": sid}]})
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    for cam in shop.get("cameras", []):
        cid = cam.get("camera_id") or cam.get("id")
        db["camera_model_assignments"].update_one(
            {"camera_id": cid, "model_id": mid},
            {"$set": {"is_running": False}}
        )

    return {"message": f"Model stopped on all cameras in shop {sid}"}

@router.get("/cameras/{camera_id}/status")
def get_camera_worker_status(camera_id: int):
    cid = int(camera_id)
    status = manager.get_status(cid)
    if not status.get("running"):
        m = motion_manager.get_status(cid)
        if m.get("running"):
            return m
    return status

@router.get("/states/all")
def get_all_camera_states(db = Depends(get_db)):
    """Returns the running state of all models and motion for all cameras."""
    states = {}
    
    # 1. Models from DB
    assignments = db["camera_model_assignments"].find()
    for a in assignments:
        cid = a.get("camera_id")
        mid = a.get("model_id")
        if cid not in states:
            states[cid] = {}
        states[cid][f"model_{mid}"] = a.get("is_running", False)
        
    # 2. Motion worker states
    motion_statuses = motion_manager.all_statuses()
    for cid, s in motion_statuses.items():
        if cid not in states:
            states[cid] = {}
        states[cid]["motion"] = s.get("running", False)

    # 3. Theft tracking state
    cameras = find_all_cameras()
    for cam in cameras:
        cid = cam.get("id")
        if cid not in states:
            states[cid] = {}
        states[cid]["theft"] = cam.get("theft_active", False)
        
    return states
