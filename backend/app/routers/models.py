from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks, status
from typing import List, Optional
import os
import uuid
from datetime import datetime
from app.database import get_db, get_next_sequence
from app.schemas.models import ModelResponse, ModelTypeCreate, ModelTypeResponse, ModelUpdate
from app.ml.validation import validate_model_task

router = APIRouter(prefix="/api/models", tags=["Models"])

UPLOAD_DIR = "/app/uploads"

TYPE_DEFAULTS = {
    "human": {"display_name": "Human Detection", "icon": "fa-user", "threshold": 0.50, "tracking": True, "cooldown": 10},
    "fire": {"display_name": "Fire & Smoke Detection", "icon": "fa-fire", "threshold": 0.55, "tracking": False, "cooldown": 30},
    "helmet": {"display_name": "Helmet / PPE Safety", "icon": "fa-hard-hat", "threshold": 0.60, "tracking": True, "cooldown": 10},
    "vehicle": {"display_name": "Vehicle Detection", "icon": "fa-car", "threshold": 0.50, "tracking": True, "cooldown": 10},
    "weapon": {"display_name": "Weapon Detection", "icon": "fa-crosshairs", "threshold": 0.65, "tracking": True, "cooldown": 10},
}

def format_model(m: dict) -> dict:
    mid = m.get("model_id") or m.get("id")
    mtype = (m.get("type") or "").lower()
    default_meta = TYPE_DEFAULTS.get(mtype, {})
    return {
        "id": mid,
        "name": m.get("name"),
        "type": m.get("type"),
        "version": m.get("version", "v1.0"),
        "file_path": m.get("file_path"),
        "status": m.get("status", "pending"),
        "is_default": m.get("is_default", False),
        "confidence_threshold": m.get("confidence_threshold", default_meta.get("threshold", 0.50)),
        "tracking_enabled": m.get("tracking_enabled", default_meta.get("tracking", True)),
        "cooldown_seconds": m.get("cooldown_seconds", default_meta.get("cooldown", 10)),
        "icon": m.get("icon") or default_meta.get("icon", "fa-cube"),
        "created_at": m.get("created_at") or datetime.utcnow()
    }

def format_model_type(mt: dict, idx: int = 1) -> dict:
    tid = mt.get("id") or mt.get("type_id") or idx
    name = mt.get("name", "").lower()
    default_meta = TYPE_DEFAULTS.get(name, {})
    return {
        "id": tid,
        "name": mt.get("name"),
        "display_name": mt.get("display_name"),
        "icon": mt.get("icon") or default_meta.get("icon", "fa-cube"),
        "default_threshold": mt.get("default_threshold", default_meta.get("threshold", 0.50)),
        "default_tracking": mt.get("default_tracking", default_meta.get("tracking", True)),
        "default_cooldown": mt.get("default_cooldown", default_meta.get("cooldown", 10))
    }

@router.post("/upload", response_model=ModelResponse, status_code=status.HTTP_201_CREATED)
async def upload_model(
    background_tasks: BackgroundTasks,
    name: str = Form(...),
    type: str = Form(...),
    file: UploadFile = File(...),
    confidence_threshold: Optional[float] = Form(None),
    tracking_enabled: Optional[bool] = Form(None),
    cooldown_seconds: Optional[int] = Form(None),
    icon: Optional[str] = Form(None),
    db = Depends(get_db)
):
    existing = db["models"].find_one({"name": name})
    if existing:
        raise HTTPException(status_code=400, detail="Model with this name already exists")
        
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    filename = f"{uuid.uuid4()}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, filename)
    
    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)
        
    model_id = get_next_sequence("model_id")
    now = datetime.utcnow()
    mtype = type.lower()
    type_defaults = TYPE_DEFAULTS.get(mtype, {})
    
    threshold_val = confidence_threshold if confidence_threshold is not None else type_defaults.get("threshold", 0.50)
    tracking_val = tracking_enabled if tracking_enabled is not None else type_defaults.get("tracking", True)
    cooldown_val = cooldown_seconds if cooldown_seconds is not None else type_defaults.get("cooldown", 10)
    icon_val = icon if icon else type_defaults.get("icon", "fa-cube")

    new_model = {
        "model_id": model_id,
        "id": model_id,
        "name": name,
        "type": type,
        "version": "v1.0",
        "file_path": file_path,
        "status": "pending",
        "is_default": False,
        "confidence_threshold": float(threshold_val),
        "tracking_enabled": bool(tracking_val),
        "cooldown_seconds": int(cooldown_val),
        "icon": icon_val,
        "created_at": now,
        "updated_at": now
    }
    db["models"].insert_one(new_model)
    
    background_tasks.add_task(validate_model_task, model_id, file_path)
    return format_model(new_model)

@router.get("", response_model=List[ModelResponse])
@router.get("/", response_model=List[ModelResponse])
def get_models(db = Depends(get_db)):
    models = db["models"].find()
    return [format_model(m) for m in models]

@router.patch("/{model_id}", response_model=ModelResponse)
@router.put("/{model_id}", response_model=ModelResponse)
def update_model(model_id: int, update_data: ModelUpdate, db = Depends(get_db)):
    mid = int(model_id)
    existing = db["models"].find_one({"$or": [{"id": mid}, {"model_id": mid}]})
    if not existing:
        raise HTTPException(status_code=404, detail="Model not found")

    update_fields = {"updated_at": datetime.utcnow()}
    if update_data.name is not None:
        update_fields["name"] = update_data.name
    if update_data.confidence_threshold is not None:
        update_fields["confidence_threshold"] = float(update_data.confidence_threshold)
    if update_data.tracking_enabled is not None:
        update_fields["tracking_enabled"] = bool(update_data.tracking_enabled)
    if update_data.cooldown_seconds is not None:
        update_fields["cooldown_seconds"] = int(update_data.cooldown_seconds)
    if update_data.icon is not None:
        update_fields["icon"] = update_data.icon

    db["models"].update_one({"_id": existing["_id"]}, {"$set": update_fields})
    updated = db["models"].find_one({"_id": existing["_id"]})
    return format_model(updated)

@router.get("/types", response_model=List[ModelTypeResponse])
def get_model_types(db = Depends(get_db)):
    types = list(db["model_types"].find())
    default_seeds = [
        {"name": "human", "display_name": "Human Detection", "icon": "fa-user", "default_threshold": 0.50, "default_tracking": True, "default_cooldown": 10},
        {"name": "fire", "display_name": "Fire & Smoke Detection", "icon": "fa-fire", "default_threshold": 0.55, "default_tracking": False, "default_cooldown": 30},
        {"name": "helmet", "display_name": "Helmet / PPE Safety", "icon": "fa-hard-hat", "default_threshold": 0.60, "default_tracking": True, "default_cooldown": 10},
        {"name": "vehicle", "display_name": "Vehicle Detection", "icon": "fa-car", "default_threshold": 0.50, "default_tracking": True, "default_cooldown": 10},
        {"name": "weapon", "display_name": "Weapon Detection", "icon": "fa-crosshairs", "default_threshold": 0.65, "default_tracking": True, "default_cooldown": 10},
    ]
    if not types:
        db["model_types"].insert_many(default_seeds)
        types = list(db["model_types"].find())
    else:
        # Check if default types need icon / default enrichment
        existing_names = {t.get("name", "").lower() for t in types}
        to_add = [d for d in default_seeds if d["name"] not in existing_names]
        if to_add:
            db["model_types"].insert_many(to_add)
            types = list(db["model_types"].find())
        
    result = []
    for i, t in enumerate(types, 1):
        result.append(format_model_type(t, i))
    return result

@router.post("/types", response_model=ModelTypeResponse, status_code=status.HTTP_201_CREATED)
def create_model_type(type_in: ModelTypeCreate, db = Depends(get_db)):
    existing = db["model_types"].find_one({"name": type_in.name})
    if existing:
        raise HTTPException(status_code=400, detail="Type already exists")
    
    tid = get_next_sequence("model_type_id")
    new_type = {
        "id": tid,
        "type_id": tid,
        "name": type_in.name,
        "display_name": type_in.display_name,
        "icon": type_in.icon or "fa-cube",
        "default_threshold": type_in.default_threshold or 0.50,
        "default_tracking": type_in.default_tracking if type_in.default_tracking is not None else True,
        "default_cooldown": type_in.default_cooldown or 10
    }
    db["model_types"].insert_one(new_type)
    return format_model_type(new_type, tid)

@router.delete("/types/{type_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model_type(type_id: int, db = Depends(get_db)):
    tid = int(type_id)
    res = db["model_types"].delete_one({"$or": [{"id": tid}, {"type_id": tid}]})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Type not found")
    return None

@router.delete("/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model(model_id: int, db = Depends(get_db)):
    mid = int(model_id)
    db_model = db["models"].find_one({"$or": [{"id": mid}, {"model_id": mid}]})
    if not db_model:
        raise HTTPException(status_code=404, detail="Model not found")
        
    # Delete dependent assignments and versions
    db["camera_model_assignments"].delete_many({"model_id": mid})
    db["model_versions"].delete_many({"model_id": mid})
        
    if db_model.get("file_path") and os.path.exists(db_model["file_path"]):
        try:
            os.remove(db_model["file_path"])
        except Exception:
            pass
            
    db["models"].delete_one({"_id": db_model["_id"]})
    return None

@router.get("/{model_id}/status")
def get_model_status(model_id: int, db = Depends(get_db)):
    mid = int(model_id)
    db_model = db["models"].find_one({"$or": [{"id": mid}, {"model_id": mid}]})
    if not db_model:
        raise HTTPException(status_code=404, detail="Model not found")
    return {"id": mid, "status": db_model.get("status", "unknown")}

@router.post("/{model_id}/activate")
def activate_model(model_id: int, db = Depends(get_db)):
    mid = int(model_id)
    res = db["models"].update_one(
        {"$or": [{"id": mid}, {"model_id": mid}]},
        {"$set": {"status": "active", "updated_at": datetime.utcnow()}}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Model not found")
    return {"message": "Model activated"}

@router.post("/{model_id}/deactivate")
def deactivate_model(model_id: int, db = Depends(get_db)):
    mid = int(model_id)
    res = db["models"].update_one(
        {"$or": [{"id": mid}, {"model_id": mid}]},
        {"$set": {"status": "inactive", "updated_at": datetime.utcnow()}}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Model not found")
    return {"message": "Model deactivated"}

@router.post("/{model_id}/test")
async def test_model_inference(
    model_id: int, 
    file: UploadFile = File(...),
    threshold: Optional[float] = Form(None),
    db = Depends(get_db)
):
    import cv2
    import numpy as np
    import base64
    from ultralytics import YOLO
    import torch

    mid = int(model_id)
    db_model = db["models"].find_one({"$or": [{"id": mid}, {"model_id": mid}]})
    if not db_model:
        raise HTTPException(status_code=404, detail="Model not found")

    file_path = db_model.get("file_path")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=400, detail="Model weights file (.pt) not found on disk")

    conf_thresh = threshold if threshold is not None else float(db_model.get("confidence_threshold", 0.50))

    contents = await file.read()
    nparr = np.frombuffer(contents, np.uint8)
    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if frame is None:
        raise HTTPException(status_code=400, detail="Invalid image file format")

    try:
        yolo = YOLO(file_path)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        results = yolo.predict(frame, conf=conf_thresh, device=device, verbose=False)
        
        annotated = frame.copy()
        detections = []
        for r in results:
            for box in r.boxes:
                conf = float(box.conf[0])
                cls_id = int(box.cls[0])
                label = yolo.names[cls_id]
                xyxy = [int(v) for v in box.xyxy[0].tolist()]

                color = (0, 0, 255) if "fire" in label.lower() or "weapon" in label.lower() else (0, 255, 0)
                cv2.rectangle(annotated, (xyxy[0], xyxy[1]), (xyxy[2], xyxy[3]), color, 2)
                tag = f"{label} {int(conf * 100)}%"
                (w, h), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
                cv2.rectangle(annotated, (xyxy[0], max(0, xyxy[1] - 22)), (xyxy[0] + w, xyxy[1]), color, -1)
                cv2.putText(annotated, tag, (xyxy[0], max(0, xyxy[1] - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

                detections.append({
                    "label": label,
                    "confidence": round(conf, 3),
                    "box": xyxy
                })

        _, buffer = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        b64_image = base64.b64encode(buffer).decode("utf-8")

        return {
            "model_name": db_model.get("name"),
            "threshold_used": conf_thresh,
            "count": len(detections),
            "detections": detections,
            "annotated_image": f"data:image/jpeg;base64,{b64_image}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Model inference failed: {str(e)}")
