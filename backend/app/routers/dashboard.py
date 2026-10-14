from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from typing import List, Optional
import psutil
from datetime import datetime, date, timedelta
import asyncio
from concurrent.futures import ThreadPoolExecutor
import cv2
import os

from app.database import get_db, find_all_cameras, update_camera_by_id
from app.workers.worker_manager import manager
from app.workers.motion_worker import MotionWorkerManager
from app.routers.worker_api import motion_manager as _motion_manager
from app.routers.trigger_api import active_cloud_workers

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])

_cached_cpu_percent = 0.0

async def update_cpu_cache():
    global _cached_cpu_percent
    while True:
        try:
            _cached_cpu_percent = await asyncio.to_thread(psutil.cpu_percent, 1.0)
        except Exception:
            pass
        await asyncio.sleep(2)

@router.on_event("startup")
async def start_background_monitoring():
    asyncio.create_task(update_cpu_cache())

def _test_rtsp_connection(rtsp_url: str) -> bool:
    if not rtsp_url:
        return False
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "timeout;2000000|rtsp_transport;tcp"
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        return False
    ret, _ = cap.read()
    cap.release()
    return ret

def get_actual_camera_statuses(cameras):
    results = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_id = {
            executor.submit(_test_rtsp_connection, cam.get("rtsp_url") or cam.get("source", "")): cam.get("id")
            for cam in cameras
        }
        for future in future_to_id:
            cam_id = future_to_id[future]
            try:
                results[cam_id] = future.result()
            except Exception:
                results[cam_id] = False

    online = sum(1 for v in results.values() if v)
    offline = len(results) - online
    return online, offline, results

@router.get("/stats")
def get_dashboard_stats(db = Depends(get_db)):
    shops_count = db["shops"].count_documents({})
    all_cams = find_all_cameras()
    cameras_count = len(all_cams)

    from app.utils.settings import get_server_mode
    server_mode = get_server_mode()

    motion_running = set()
    motion_error = set()
    yolo_running = set()

    if server_mode == "cloud":
        try:
            items = list(active_cloud_workers.items())
            dead_keys = [k for k, w in items if not w.is_alive()]
            for k in dead_keys:
                if k in active_cloud_workers:
                    del active_cloud_workers[k]
        except RuntimeError:
            pass
        
        active_trigger_cams = {cam_id for (cam_id, _), w in list(active_cloud_workers.items()) if w.is_alive()}
        cameras_online = len(active_trigger_cams)
        cameras_error = 0
        cameras_offline = cameras_count - cameras_online
    else:
        all_statuses = _motion_manager.all_statuses()
        motion_running = set(_motion_manager.running_camera_ids())
        motion_error   = set(_motion_manager.error_camera_ids())
        yolo_running   = {cid for cid, w in manager.workers.items() if w.running}

        cameras_online  = len((motion_running | yolo_running))
        cameras_error   = len(motion_error - yolo_running)
        cameras_offline = max(0, cameras_count - cameras_online - cameras_error)

    # Today's date boundary
    today_start = datetime.combine(date.today(), datetime.min.time())
    today_end = today_start + timedelta(days=1)
    
    # Aggregation for today's detection counts
    pipeline = [
        {"$match": {"detected_at": {"$gte": today_start, "$lt": today_end}}},
        {"$group": {"_id": "$detection_type", "count": {"$sum": 1}}}
    ]
    detections = db["detection_events"].aggregate(pipeline)
    detection_counts = {item["_id"]: item["count"] for item in detections if item["_id"]}

    # Motion card = number of cameras currently actively detecting motion (within last 1 min)
    one_min_ago = datetime.utcnow() - timedelta(minutes=1)
    active_motion_cams = db["detection_events"].distinct(
        "camera_id",
        {"detection_type": "motion", "ended_at": None, "detected_at": {"$gte": one_min_ago}}
    )
    active_motion_count = len(active_motion_cams)
    detection_counts['Motion'] = active_motion_count
    if 'motion' in detection_counts:
        del detection_counts['motion']

    # Detailed detections based on ModelTypes
    detailed_detections = []
    model_types = list(db["model_types"].find())
    core_motion_added = False
    
    for mt in model_types:
        mt_name = mt.get("name")
        display_name = mt.get("display_name")
        if mt_name in ('mog2', 'motion'):
            core_motion_added = True
            cameras_running_count = len(motion_running)
            actual_type = "motion"
        elif mt_name == "yolo_human":
            actual_type = "human"
            cameras_running_count = db["camera_model_assignments"].count_documents({
                "is_running": True
            })
        else:
            actual_type = mt_name
            cameras_running_count = db["camera_model_assignments"].count_documents({
                "is_running": True
            })

        total = db["detection_events"].count_documents({
            "detection_type": actual_type,
            "detected_at": {"$gte": today_start, "$lt": today_end}
        })
        
        active_cams = db["detection_events"].distinct(
            "camera_id",
            {"detection_type": actual_type, "ended_at": None, "detected_at": {"$gte": one_min_ago}}
        )
        
        detailed_detections.append({
            "type": mt_name,
            "display_name": display_name,
            "active_now": len(active_cams),
            "total_today": total,
            "cameras_running": cameras_running_count
        })

    if not core_motion_added:
        mot_total = db["detection_events"].count_documents({
            "detection_type": "motion",
            "detected_at": {"$gte": today_start, "$lt": today_end}
        })
        detailed_detections.insert(0, {
            "type": "motion",
            "display_name": "Motion",
            "active_now": active_motion_count,
            "total_today": mot_total,
            "cameras_running": len(motion_running)
        })

    cpu_percent = _cached_cpu_percent
    ram = psutil.virtual_memory()
    ram_percent = ram.percent
    ram_used_gb = round(ram.used / (1024**3), 2)

    temperature = None
    try:
        if hasattr(psutil, "sensors_temperatures"):
            temps = psutil.sensors_temperatures()
            if temps and 'coretemp' in temps:
                temperature = temps['coretemp'][0].current
            elif temps and temps.keys():
                first_key = list(temps.keys())[0]
                temperature = temps[first_key][0].current
    except Exception:
        pass

    def _get_gpu_percent():
        try:
            import subprocess
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2
            )
            if result.returncode == 0:
                val = result.stdout.strip().split("\n")[0].strip()
                return float(val)
        except Exception:
            pass
        return 0.0

    def _get_gpu_memory():
        try:
            import subprocess
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split("\n")[0].split(",")
                return float(parts[0].strip()), float(parts[1].strip())
        except Exception:
            pass
        return 0.0, 0.0

    gpu_percent = _get_gpu_percent()
    gpu_used_mb, gpu_total_mb = _get_gpu_memory()

    return {
        "shops_count": shops_count,
        "cameras_count": cameras_count,
        "cameras_online": cameras_online,
        "cameras_offline": cameras_offline,
        "cameras_error": cameras_error,
        "detection_counts": detection_counts,
        "detailed_detections": detailed_detections,
        "system": {
            "cpu_percent": cpu_percent,
            "ram_percent": ram_percent,
            "ram_used_gb": ram_used_gb,
            "gpu_percent": gpu_percent,
            "gpu_used_mb": gpu_used_mb,
            "gpu_total_mb": gpu_total_mb,
            "temperature": temperature
        }
    }

@router.get("/camera-status")
def get_camera_status():
    """
    Per-camera status: online / error / offline.
    Uses motion worker state (stream_ok + error_msg) — fast, no new blocking TCP probe.
    """
    all_cams = find_all_cameras(filter_active_only=True)
    all_statuses = _motion_manager.all_statuses()
    result = {}
    for cam in all_cams:
        cid = cam.get("id")
        w = all_statuses.get(cid, {})
        if w.get("running"):
            state = "online"
        elif w.get("error"):
            state = "error"
        else:
            state = "offline"
        result[cid] = {
            "online": state == "online",
            "state": state,
            "error": w.get("error")
        }
    return result

@router.get("/motion-status")
@router.get("/camera-motion-status")
def get_all_camera_motion_status(db = Depends(get_db)):
    one_min_ago = datetime.utcnow() - timedelta(minutes=1)
    all_cams = find_all_cameras()
    
    recent_events = db["detection_events"].find({
        "detection_type": "motion",
        "ended_at": None,
        "detected_at": {"$gte": one_min_ago}
    })
    active_ev_map = {ev.get("camera_id"): ev for ev in recent_events}

    result = []
    for cam in all_cams:
        cid = cam.get("id")
        ev = active_ev_map.get(cid)
        result.append({
            "camera_id": cid,
            "camera_name": cam.get("name"),
            "shop_id": cam.get("shop_id"),
            "shop_name": cam.get("shop_name", "-"),
            "active": ev is not None,
            "started_at": ev.get("detected_at") if ev else None,
        })

    result.sort(key=lambda r: (r["shop_name"] or "", r["camera_name"] or ""))
    return result

@router.get("/detections")
def get_recent_detections(type: str = None, date_str: str = None, limit: int = 50, active_only: bool = False, db = Depends(get_db)):
    query = {}
    if type:
        query["detection_type"] = {"$regex": f"^{type}$", "$options": "i"}

    if active_only:
        one_min_ago = datetime.utcnow() - timedelta(minutes=1)
        query["ended_at"] = None
        query["detected_at"] = {"$gte": one_min_ago}

    if date_str:
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            start = datetime.combine(target_date, datetime.min.time())
            end = start + timedelta(days=1)
            query["detected_at"] = {"$gte": start, "$lt": end}
        except ValueError:
            pass

    events = db["detection_events"].find(query).sort("detected_at", -1).limit(limit)

    return [
        {
            "id": e.get("event_id") or str(e["_id"]),
            "camera_name": e.get("camera_name"),
            "shop_name": e.get("shop_name"),
            "detected_at": e.get("detected_at"),
            "ended_at": e.get("ended_at"),
            "detection_type": e.get("detection_type"),
            "metadata": e.get("metadata")
        } for e in events
    ]

class ReviewUpdate(BaseModel):
    review_state: str

@router.post("/detections/{event_id}/review")
def update_detection_review(event_id: str, payload: ReviewUpdate, db = Depends(get_db)):
    from bson import ObjectId
    q = {}
    try:
        q = {"_id": ObjectId(event_id)}
    except Exception:
        try:
            q = {"event_id": int(event_id)}
        except Exception:
            q = {"event_id": event_id}

    res = db["detection_events"].update_one(
        q,
        {"$set": {"metadata.review_state": payload.review_state}}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Event not found")
    return {"status": "success", "review_state": payload.review_state}

@router.post("/test-video")
async def test_video_pipeline(
    shop_name: str = Form(...),
    camera_name: str = Form(...),
    video: UploadFile = File(...),
):
    await asyncio.sleep(2)
    return {
        "status": "success",
        "score": 0.89,
        "state": "confirmed",
        "message": "Anomaly detected in dummy pipeline."
    }

from app.utils.settings import get_server_mode, set_server_mode

@router.get("/settings")
def get_dashboard_settings():
    return {"server_mode": get_server_mode()}

class SettingsUpdate(BaseModel):
    server_mode: str

@router.post("/settings")
def update_dashboard_settings(payload: SettingsUpdate):
    if payload.server_mode not in ["standalone", "cloud"]:
        raise HTTPException(status_code=400, detail="Invalid server mode")
        
    old_mode = get_server_mode()
    set_server_mode(payload.server_mode)
    
    if old_mode != payload.server_mode:
        active_cameras = find_all_cameras(filter_active_only=True)
        
        if payload.server_mode == "cloud":
            for cam in active_cameras:
                _motion_manager.stop(cam["id"])
                manager.stop_worker(cam["id"])
        elif payload.server_mode == "standalone":
            for cam in active_cameras:
                if cam.get('motion_active', False):
                    rtsp = cam.get("rtsp_url") or cam.get("source", "")
                    _motion_manager.start(cam["id"], rtsp, cam.get("shop_id"), cam.get("name"))
                    
    return {"status": "success", "server_mode": payload.server_mode}

class SystemSettingsUpdate(BaseModel):
    cloud_trigger_url: str

@router.get("/system_settings")
def get_system_settings(db = Depends(get_db)):
    setting = db["system_settings"].find_one({"key": "cloud_trigger_url"})
    return {"cloud_trigger_url": setting.get("value") if setting else "http://13.48.57.153:8000/api/trigger/"}

@router.post("/system_settings")
def update_system_settings(payload: SystemSettingsUpdate, db = Depends(get_db)):
    db["system_settings"].update_one(
        {"key": "cloud_trigger_url"},
        {"$set": {"value": payload.cloud_trigger_url, "updated_at": datetime.utcnow()}},
        upsert=True
    )
    return {"status": "success", "cloud_trigger_url": payload.cloud_trigger_url}