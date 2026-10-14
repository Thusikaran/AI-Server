from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks, status
from typing import List, Optional
import os
import uuid
from datetime import datetime
from app.database import get_db, get_next_sequence
from app.schemas.models import ModelResponse, ModelTypeCreate, ModelTypeResponse
from app.ml.validation import validate_model_task

router = APIRouter(prefix="/api/models", tags=["Models"])

UPLOAD_DIR = "/app/uploads"

def format_model(m: dict) -> dict:
    mid = m.get("model_id") or m.get("id")
    return {
        "id": mid,
        "name": m.get("name"),
        "type": m.get("type"),
        "version": m.get("version", "v1.0"),
        "file_path": m.get("file_path"),
        "status": m.get("status", "pending"),
        "is_default": m.get("is_default", False),
        "created_at": m.get("created_at") or datetime.utcnow()
    }

def format_model_type(mt: dict, idx: int = 1) -> dict:
    tid = mt.get("id") or mt.get("type_id") or idx
    return {
        "id": tid,
        "name": mt.get("name"),
        "display_name": mt.get("display_name")
    }

@router.post("/upload", response_model=ModelResponse, status_code=status.HTTP_201_CREATED)
async def upload_model(
    background_tasks: BackgroundTasks,
    name: str = Form(...),
    type: str = Form(...),
    file: UploadFile = File(...),
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
    new_model = {
        "model_id": model_id,
        "id": model_id,
        "name": name,
        "type": type,
        "version": "v1.0",
        "file_path": file_path,
        "status": "pending",
        "is_default": False,
        "created_at": now,
        "updated_at": now
    }
    db["models"].insert_one(new_model)
    
    background_tasks.add_task(validate_model_task, model_id, file_path)
    return format_model(new_model)

@router.get("/", response_model=List[ModelResponse])
def get_models(db = Depends(get_db)):
    models = db["models"].find()
    return [format_model(m) for m in models]

@router.get("/types", response_model=List[ModelTypeResponse])
def get_model_types(db = Depends(get_db)):
    types = list(db["model_types"].find())
    if not types:
        defaults = [
            {"name": "theft", "display_name": "Theft & Shoplifting Detection"},
            {"name": "motion", "display_name": "Motion Detection"},
            {"name": "intrusion", "display_name": "Area Intrusion Detection"},
            {"name": "loitering", "display_name": "Loitering Detection"},
        ]
        db["model_types"].insert_many(defaults)
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
        "display_name": type_in.display_name
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
