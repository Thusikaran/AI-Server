import os
import cv2
import numpy as np
from app.database import get_db
import traceback
from datetime import datetime

def validate_model_task(model_id: int, file_path: str):
    db = get_db()
    mid = int(model_id)
    try:
        model_db = db["models"].find_one({"$or": [{"id": mid}, {"model_id": mid}]})
        if not model_db:
            return

        db["models"].update_one(
            {"_id": model_db["_id"]},
            {"$set": {"status": "validating", "updated_at": datetime.utcnow()}}
        )

        # Check extension
        if not file_path.endswith('.pt'):
            db["models"].update_one(
                {"_id": model_db["_id"]},
                {"$set": {"status": "failed", "updated_at": datetime.utcnow()}}
            )
            return
            
        try:
            from ultralytics import YOLO
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"

            # Load model
            yolo_model = YOLO(file_path)
            
            # Test inference on blank frame
            blank_frame = np.zeros((480, 640, 3), dtype=np.uint8)
            results = yolo_model.predict(source=blank_frame, imgsz=640, conf=0.5, verbose=False, device=device)
            
            # If we get here without crashing, model is reasonably valid
            db["models"].update_one(
                {"_id": model_db["_id"]},
                {"$set": {"status": "active", "updated_at": datetime.utcnow()}}
            )
            
            # Create a model version record
            db["model_versions"].insert_one({
                "model_id": mid,
                "version_tag": model_db.get("version", "v1.0"),
                "is_current": True,
                "deployed_at": datetime.utcnow()
            })
            
        except Exception as e:
            print(f"Validation failed for model {model_id}: {traceback.format_exc()}")
            db["models"].update_one(
                {"_id": model_db["_id"]},
                {"$set": {"status": "failed", "updated_at": datetime.utcnow()}}
            )
    except Exception as ex:
        print(f"Error during validate_model_task: {ex}")
