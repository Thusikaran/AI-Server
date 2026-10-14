from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
import pandas as pd
from io import BytesIO
from datetime import datetime
from app.database import get_db, get_next_sequence

router = APIRouter(prefix="/api/bulk-insert", tags=["Bulk Insert"])

@router.post("/")
async def bulk_insert(file: UploadFile = File(...), db = Depends(get_db)):
    if not (file.filename.endswith('.csv') or file.filename.endswith('.xlsx')):
        raise HTTPException(status_code=400, detail="Only .csv and .xlsx files are supported")
        
    contents = await file.read()
    try:
        if file.filename.endswith('.csv'):
            df = pd.read_csv(BytesIO(contents))
        else:
            df = pd.read_excel(BytesIO(contents))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error parsing file: {str(e)}")
        
    required_cols_v1 = {'shop_name', 'camera_name', 'rtsp_url'}
    required_cols_v2 = {'shop_name', 'display_name', 'rtsp_url'}
    
    col_set = set(df.columns)
    cam_col = None
    if required_cols_v1.issubset(col_set):
        cam_col = 'camera_name'
    elif required_cols_v2.issubset(col_set):
        cam_col = 'display_name'
    else:
        raise HTTPException(status_code=400, detail="File must contain columns: (shop_name, camera_name or display_name, rtsp_url)")
        
    stats = {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0}
    now = datetime.utcnow()
    
    for index, row in df.iterrows():
        shop_name = str(row['shop_name']).strip()
        camera_name = str(row[cam_col]).strip()
        rtsp_url = str(row['rtsp_url']).strip()
        
        if not shop_name or not camera_name or not rtsp_url or shop_name == 'nan' or camera_name == 'nan':
            stats["skipped"] += 1
            continue
            
        try:
            # 1. Upsert Shop
            shop = db["shops"].find_one({"name": shop_name})
            if not shop:
                new_sid = get_next_sequence("shop_id")
                shop = {
                    "shop_id": new_sid,
                    "id": new_sid,
                    "name": shop_name,
                    "cameras": [],
                    "created_at": now,
                    "updated_at": now
                }
                db["shops"].insert_one(shop)
            
            # 2. Check if camera exists in shop's cameras array
            shop_id = shop.get("shop_id") or shop.get("id")
            existing_cam = None
            for c in shop.get("cameras", []):
                if c.get("name") == camera_name:
                    existing_cam = c
                    break
                    
            if not existing_cam:
                new_cid = get_next_sequence("camera_id")
                new_cam = {
                    "camera_id": new_cid,
                    "id": new_cid,
                    "shop_id": shop_id,
                    "name": camera_name,
                    "rtsp_url": rtsp_url,
                    "source": rtsp_url,
                    "is_active": True,
                    "motion_active": False,
                    "theft_active": False,
                    "assignments": [],
                    "created_at": now,
                    "updated_at": now
                }
                db["shops"].update_one(
                    {"_id": shop["_id"]},
                    {"$push": {"cameras": new_cam}, "$set": {"updated_at": now}}
                )
                stats["inserted"] += 1
            else:
                if existing_cam.get("rtsp_url") != rtsp_url:
                    db["shops"].update_one(
                        {"_id": shop["_id"], "cameras.name": camera_name},
                        {"$set": {"cameras.$.rtsp_url": rtsp_url, "cameras.$.source": rtsp_url, "cameras.$.updated_at": now}}
                    )
                    stats["updated"] += 1
                else:
                    stats["skipped"] += 1
        except Exception as e:
            stats["errors"] += 1
            
    return {"message": "Bulk insert processed", "stats": stats}
