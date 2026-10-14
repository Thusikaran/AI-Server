import threading
import time
import cv2
import traceback
import numpy as np
import os
import collections

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
from datetime import datetime
from app.database import get_db, find_camera_by_id, get_next_sequence
from app.workers.tracker import HumanTracker
from ultralytics import YOLO

class CameraWorker(threading.Thread):
    def __init__(self, camera_id: int):
        super().__init__()
        self.camera_id = int(camera_id)
        self.running = False
        self.error_msg = None
        self.models_cache = {} 
        self.motion_threshold = 2000 
        self.frame_buffer = collections.deque(maxlen=150)

    def stop(self):
        self.running = False

    def load_active_models(self, db):
        assignments = list(db["camera_model_assignments"].find({
            "camera_id": self.camera_id,
            "is_running": True
        }))
        active_model_ids = [a.get("model_id") for a in assignments]
        
        for m_id in list(self.models_cache.keys()):
            if m_id not in active_model_ids:
                del self.models_cache[m_id]
                
        active_db_models = []
        for a in assignments:
            mid = a.get("model_id")
            model = db["models"].find_one({"$or": [{"id": mid}, {"model_id": mid}]})
            if model and model.get("file_path") and model.get("status") == 'active':
                active_db_models.append(model)
                if mid not in self.models_cache:
                    try:
                        self.models_cache[mid] = {
                            "yolo": YOLO(model["file_path"]),
                            "type": model.get("type"),
                            "db_model": model
                        }
                    except Exception as e:
                        print(f"Failed to load model {mid}: {e}")
                        
        return active_db_models

    def run(self):
        self.running = True
        db = get_db()
        try:
            camera = find_camera_by_id(self.camera_id)
            if not camera:
                self.error_msg = "Camera not found"
                return
            
            shop_id = camera.get("shop_id")
            shop = db["shops"].find_one({"$or": [{"shop_id": shop_id}, {"id": shop_id}]})
            shop_name = shop.get("name") if shop else "Unknown"
            
            rtsp_url = camera.get("rtsp_url") or camera.get("source", "")
            
            cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
            mog2 = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=16, detectShadow=True)
            tracker = HumanTracker()
            
            self.load_active_models(db)
            frame_count = 0
            
            while self.running:
                ret, frame = cap.read()
                if not ret:
                    time.sleep(5)
                    cap.release()
                    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
                    continue
                    
                frame_count += 1
                self.frame_buffer.append(frame)
                
                if frame_count % 60 == 0:
                    self.load_active_models(db)
                    
                fg_mask = mog2.apply(frame)
                motion_area = np.sum(fg_mask > 200) 
                
                if motion_area < self.motion_threshold and len(tracker.tracks) == 0:
                    new_tracks, lost_event_ids = tracker.update([])
                    if lost_event_ids:
                        now = datetime.utcnow()
                        for ev_id in lost_event_ids:
                            db["detection_events"].update_one(
                                {"$or": [{"event_id": ev_id}, {"id": ev_id}]},
                                {"$set": {"ended_at": now}}
                            )
                    continue
                
                human_detections = []
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"

                for m_id, m_data in self.models_cache.items():
                    yolo = m_data["yolo"]
                    results = yolo.predict(frame, verbose=False, conf=0.5, device=device)
                    
                    for r in results:
                        for box in r.boxes:
                            cls_id = int(box.cls[0])
                            conf = float(box.conf[0])
                            label = yolo.names[cls_id]
                            xyxy = box.xyxy[0].tolist()
                            
                            if m_data["type"] == "yolo_human" and label == "person":
                                human_detections.append(xyxy)
                            else:
                                now = datetime.utcnow()
                                event_id = get_next_sequence("event_id")
                                new_event = {
                                    "event_id": event_id,
                                    "camera_id": self.camera_id,
                                    "model_id": m_id,
                                    "shop_id": shop_id,
                                    "shop_name": shop_name,
                                    "camera_name": camera.get("name"),
                                    "detection_type": m_data["type"],
                                    "confidence": conf,
                                    "detected_at": now,
                                    "ended_at": now,
                                    "metadata": {}
                                }
                                db["detection_events"].insert_one(new_event)

                new_track_ids, lost_event_ids = tracker.update(human_detections)
                
                if lost_event_ids:
                    now = datetime.utcnow()
                    for ev_id in lost_event_ids:
                        db["detection_events"].update_one(
                            {"$or": [{"event_id": ev_id}, {"id": ev_id}]},
                            {"$set": {"ended_at": now}}
                        )
                
                if new_track_ids:
                    human_model_id = None
                    for m_id, m_data in self.models_cache.items():
                        if m_data["type"] == "yolo_human":
                            human_model_id = m_id
                            break
                            
                    for t_id in new_track_ids:
                        now = datetime.utcnow()
                        event_id = get_next_sequence("event_id")
                        new_event = {
                            "event_id": event_id,
                            "camera_id": self.camera_id,
                            "model_id": human_model_id,
                            "shop_id": shop_id,
                            "shop_name": shop_name,
                            "camera_name": camera.get("name"),
                            "detection_type": "human",
                            "confidence": 1.0,
                            "detected_at": now,
                            "ended_at": None,
                            "metadata": {}
                        }
                        db["detection_events"].insert_one(new_event)
                        tracker.set_db_event_id(t_id, event_id)
                        
        except Exception as e:
            self.error_msg = str(e)
            print(f"Worker {self.camera_id} crashed: {traceback.format_exc()}")
        finally:
            if 'cap' in locals() and cap is not None:
                cap.release()
            self.running = False
