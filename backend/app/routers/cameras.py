from fastapi import APIRouter, Depends, HTTPException, status
from typing import List
from datetime import datetime
from app.database import get_db, find_all_cameras, find_camera_by_id, update_camera_by_id
from app.schemas.shop_camera import CameraUpdate, CameraResponse

router = APIRouter(prefix="/api/cameras", tags=["Cameras"])

def format_camera(c: dict) -> dict:
    cid = c.get("camera_id") or c.get("id")
    return {
        "id": cid,
        "shop_id": c.get("shop_id", 0),
        "name": c.get("name"),
        "rtsp_url": c.get("rtsp_url") or c.get("source", ""),
        "is_active": c.get("is_active", True),
        "created_at": c.get("created_at") or datetime.utcnow(),
        "updated_at": c.get("updated_at") or datetime.utcnow()
    }

@router.get("/", response_model=List[CameraResponse])
def get_all_cameras():
    cameras = find_all_cameras()
    return [format_camera(c) for c in cameras]

@router.put("/{camera_id}", response_model=CameraResponse)
def update_camera(camera_id: int, camera: CameraUpdate, db = Depends(get_db)):
    cid = int(camera_id)
    cam = find_camera_by_id(cid)
    if not cam:
        raise HTTPException(status_code=404, detail="Camera not found")
        
    shop_id = cam.get("shop_id")
    updates = {"updated_at": datetime.utcnow()}
    
    if camera.name is not None:
        # Check name conflict in the same shop
        shop = db["shops"].find_one({"$or": [{"shop_id": shop_id}, {"id": shop_id}]})
        if shop:
            for other_cam in shop.get("cameras", []):
                other_id = other_cam.get("camera_id") or other_cam.get("id")
                if other_id != cid and other_cam.get("name") == camera.name:
                    raise HTTPException(status_code=400, detail="Camera with this name already exists in this shop")
        updates["name"] = camera.name
        cam["name"] = camera.name

    if camera.rtsp_url is not None:
        updates["rtsp_url"] = camera.rtsp_url
        updates["source"] = camera.rtsp_url
        cam["rtsp_url"] = camera.rtsp_url

    if camera.is_active is not None:
        updates["is_active"] = camera.is_active
        cam["is_active"] = camera.is_active

    update_camera_by_id(cid, updates)
    return format_camera(cam)

@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_camera(camera_id: int, db = Depends(get_db)):
    cid = int(camera_id)
    res = db["shops"].update_one(
        {"cameras": {"$elemMatch": {"$or": [{"camera_id": cid}, {"id": cid}]}}},
        {"$pull": {"cameras": {"$or": [{"camera_id": cid}, {"id": cid}]}}}
    )
    if res.modified_count == 0:
        raise HTTPException(status_code=404, detail="Camera not found")
    return None
