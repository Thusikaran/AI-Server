from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional
from datetime import datetime, timedelta
from app.database import get_db, get_next_sequence
from app.schemas.shop_camera import ShopCreate, ShopUpdate, ShopResponse, CameraCreate, CameraResponse

router = APIRouter(prefix="/api/shops", tags=["Shops"])

def format_shop(s: dict) -> dict:
    return {
        "id": s.get("shop_id") or s.get("id"),
        "name": s.get("name"),
        "created_at": s.get("created_at") or datetime.utcnow(),
        "updated_at": s.get("updated_at") or datetime.utcnow()
    }

def format_camera(c: dict, shop_id: int) -> dict:
    cid = c.get("camera_id") or c.get("id")
    return {
        "id": cid,
        "shop_id": shop_id,
        "name": c.get("name"),
        "rtsp_url": c.get("rtsp_url") or c.get("source", ""),
        "is_active": c.get("is_active", True),
        "created_at": c.get("created_at") or datetime.utcnow(),
        "updated_at": c.get("updated_at") or datetime.utcnow()
    }

@router.post("/", response_model=ShopResponse, status_code=status.HTTP_201_CREATED)
def create_shop(shop: ShopCreate, db = Depends(get_db)):
    existing = db["shops"].find_one({"name": shop.name})
    if existing:
        raise HTTPException(status_code=400, detail="Shop with this name already exists")
    
    new_shop_id = get_next_sequence("shop_id")
    now = datetime.utcnow()
    new_shop_doc = {
        "shop_id": new_shop_id,
        "id": new_shop_id,
        "name": shop.name,
        "cameras": [],
        "created_at": now,
        "updated_at": now
    }
    db["shops"].insert_one(new_shop_doc)
    return format_shop(new_shop_doc)

@router.get("/", response_model=List[ShopResponse])
def get_shops(db = Depends(get_db)):
    shops = db["shops"].find()
    return [format_shop(s) for s in shops]

@router.put("/{shop_id}", response_model=ShopResponse)
def update_shop(shop_id: int, shop: ShopUpdate, db = Depends(get_db)):
    sid = int(shop_id)
    existing_shop = db["shops"].find_one({"$or": [{"shop_id": sid}, {"id": sid}]})
    if not existing_shop:
        raise HTTPException(status_code=404, detail="Shop not found")
    
    conflict = db["shops"].find_one({"name": shop.name, "shop_id": {"$ne": sid}})
    if conflict:
        raise HTTPException(status_code=400, detail="Shop with this name already exists")
        
    now = datetime.utcnow()
    db["shops"].update_one(
        {"_id": existing_shop["_id"]},
        {"$set": {"name": shop.name, "updated_at": now}}
    )
    existing_shop["name"] = shop.name
    existing_shop["updated_at"] = now
    return format_shop(existing_shop)

@router.delete("/{shop_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shop(shop_id: int, db = Depends(get_db)):
    sid = int(shop_id)
    res = db["shops"].delete_one({"$or": [{"shop_id": sid}, {"id": sid}]})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Shop not found")
    return None

@router.post("/{shop_id}/cameras", response_model=CameraResponse, status_code=status.HTTP_201_CREATED)
def add_camera_to_shop(shop_id: int, camera: CameraCreate, db = Depends(get_db)):
    sid = int(shop_id)
    shop = db["shops"].find_one({"$or": [{"shop_id": sid}, {"id": sid}]})
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
        
    for c in shop.get("cameras", []):
        if c.get("name") == camera.name:
            raise HTTPException(status_code=400, detail="Camera with this name already exists in this shop")
            
    new_cam_id = get_next_sequence("camera_id")
    now = datetime.utcnow()
    new_cam_doc = {
        "camera_id": new_cam_id,
        "id": new_cam_id,
        "shop_id": sid,
        "name": camera.name,
        "rtsp_url": camera.rtsp_url,
        "source": camera.rtsp_url,
        "is_active": camera.is_active if camera.is_active is not None else True,
        "motion_active": False,
        "theft_active": False,
        "assignments": [],
        "created_at": now,
        "updated_at": now
    }
    
    db["shops"].update_one(
        {"_id": shop["_id"]},
        {"$push": {"cameras": new_cam_doc}, "$set": {"updated_at": now}}
    )
    return format_camera(new_cam_doc, sid)

@router.get("/{shop_id}/cameras", response_model=List[CameraResponse])
def get_cameras_for_shop(shop_id: int, db = Depends(get_db)):
    sid = int(shop_id)
    shop = db["shops"].find_one({"$or": [{"shop_id": sid}, {"id": sid}]})
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")
    return [format_camera(c, sid) for c in shop.get("cameras", [])]

@router.get("/cameras/{camera_id}/timeline")
def get_camera_timeline(camera_id: int, date: Optional[str] = None, db = Depends(get_db)):
    cid = int(camera_id)
    target_date = datetime.strptime(date, "%Y-%m-%d").date() if date else datetime.utcnow().date()
    start_of_day = datetime.combine(target_date, datetime.min.time())
    end_of_day = start_of_day + timedelta(days=1)

    events = db["detection_events"].find({
        "camera_id": cid,
        "detected_at": {"$gte": start_of_day, "$lt": end_of_day}
    }).sort("detected_at", 1)

    detection_periods = []
    for ev in events:
        detected_at = ev.get("detected_at")
        ended_at = ev.get("ended_at")
        end_time = ended_at.isoformat() if ended_at else (detected_at + timedelta(seconds=1)).isoformat()
        detection_periods.append({
            "start": detected_at.isoformat() if detected_at else None,
            "end": end_time,
            "type": ev.get("detection_type"),
            "camera_name": ev.get("camera_name")
        })

    return {
        "active_periods": [],
        "detection_periods": detection_periods
    }
