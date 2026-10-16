import threading
import time
import cv2
import traceback
import numpy as np
import os
import collections
import torch
from pathlib import Path
from datetime import datetime

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
from app.database import get_db, find_camera_by_id, get_next_sequence
from app.workers.tracker import HumanTracker
from ultralytics import YOLO

SNAPSHOT_DIR = "/app/uploads/snapshots"

class CameraWorker(threading.Thread):
    def __init__(self, camera_id: int):
        super().__init__()
        self.camera_id = int(camera_id)
        self.running = False
        self.error_msg = None
        self.models_cache = {} 
        self.motion_threshold = 1200 
        self.frame_buffer = collections.deque(maxlen=150)
        
        # Motion Hold: Hold detection for 10 seconds after motion stops
        self.last_motion_time = 0
        self.motion_hold_seconds = 10
        
        # Deduplication & Cooldown Tracking
        # { (model_id, track_id_or_class): last_snapshot_timestamp }
        self.last_snapshot_time = {}
        # Trackers per model: { model_id: HumanTracker() }
        self.model_trackers = {}
        
        # Fire State Machine: { model_id: { "consecutive_frames": int, "active": bool, "last_shot": float, "last_area": float } }
        self.fire_states = {}

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
                if m_id in self.model_trackers:
                    del self.model_trackers[m_id]
                if m_id in self.fire_states:
                    del self.fire_states[m_id]
                
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
                            "type": model.get("type", "").lower(),
                            "db_model": model,
                            "threshold": float(model.get("confidence_threshold", 0.50)),
                            "tracking": bool(model.get("tracking_enabled", True)),
                            "cooldown": int(model.get("cooldown_seconds", 10)),
                            "icon": model.get("icon", "fa-cube")
                        }
                        if self.models_cache[mid]["tracking"]:
                            self.model_trackers[mid] = HumanTracker()
                    except Exception as e:
                        print(f"[CameraWorker] Failed to load model {mid}: {e}", flush=True)
                else:
                    # Update dynamic runtime thresholds if changed via UI
                    self.models_cache[mid]["threshold"] = float(model.get("confidence_threshold", 0.50))
                    self.models_cache[mid]["tracking"] = bool(model.get("tracking_enabled", True))
                    self.models_cache[mid]["cooldown"] = int(model.get("cooldown_seconds", 10))
                    self.models_cache[mid]["icon"] = model.get("icon", "fa-cube")
                        
        return active_db_models

    def _save_snapshot(self, frame, detection_type, label, confidence, box_xyxy=None):
        """Draws bounding box with label and saves JPEG to /app/uploads/snapshots."""
        try:
            today_str = datetime.utcnow().strftime("%Y-%m-%d")
            out_dir = os.path.join(SNAPSHOT_DIR, today_str)
            os.makedirs(out_dir, exist_ok=True)
            
            annotated_frame = frame.copy()
            if box_xyxy:
                x1, y1, x2, y2 = [int(v) for v in box_xyxy]
                color = (0, 0, 255) if 'fire' in detection_type or 'weapon' in detection_type else (0, 255, 0)
                cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), color, 2)
                tag = f"{label} {int(confidence * 100)}%"
                (w, h), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
                cv2.rectangle(annotated_frame, (x1, max(0, y1 - 22)), (x1 + w, y1), color, -1)
                cv2.putText(annotated_frame, tag, (x1, max(0, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            
            ts = int(time.time() * 1000)
            fname = f"snap_cam{self.camera_id}_{detection_type}_{ts}.jpg"
            abs_path = os.path.join(out_dir, fname)
            cv2.imwrite(abs_path, annotated_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
            return f"/api/snapshots/{today_str}/{fname}"
        except Exception as e:
            print(f"[CameraWorker] Error saving snapshot: {e}", flush=True)
            return None

    def run(self):
        self.running = True
        db = get_db()
        cap = None
        device = "cuda" if torch.cuda.is_available() else "cpu"
        
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
            mog2 = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=16, detectShadows=True)
            
            self.load_active_models(db)
            frame_count = 0
            
            while self.running:
                ret, frame = cap.read()
                if not ret:
                    time.sleep(3)
                    cap.release()
                    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
                    continue
                    
                frame_count += 1
                now_ts = time.time()
                
                # Check models cache periodically
                if frame_count % 90 == 0:
                    self.load_active_models(db)
                
                if not self.models_cache:
                    time.sleep(0.5)
                    continue
                
                # ── Motion Gating with 10s Hold Time ──
                # Check motion on scaled frame
                small_frame = cv2.resize(frame, (640, 360))
                fg_mask = mog2.apply(small_frame)
                motion_pixels = int(np.sum(fg_mask > 200))
                
                if motion_pixels >= self.motion_threshold:
                    self.last_motion_time = now_ts
                
                is_motion_active = (now_ts - self.last_motion_time) <= self.motion_hold_seconds
                
                # If motion has been dead for > 10s, skip AI inference to preserve GPU
                if not is_motion_active:
                    continue
                
                # Run AI inference ~6-10 FPS (every 3 frames)
                if frame_count % 3 != 0:
                    continue
                
                for m_id, m_data in list(self.models_cache.items()):
                    yolo = m_data["yolo"]
                    m_type = m_data["type"]
                    threshold = m_data["threshold"]
                    cooldown = m_data["cooldown"]
                    tracking = m_data["tracking"]
                    
                    results = yolo.predict(frame, verbose=False, conf=threshold, device=device)
                    
                    # ── FIRE DETECTION LOGIC (Multi-frame confirmation & periodic progress) ──
                    if "fire" in m_type:
                        f_state = self.fire_states.setdefault(m_id, {"consecutive": 0, "active": False, "last_shot": 0, "last_area": 0})
                        fire_boxes = []
                        max_conf = 0.0
                        total_area = 0.0
                        
                        for r in results:
                            for box in r.boxes:
                                c = float(box.conf[0])
                                xyxy = box.xyxy[0].tolist()
                                area = (xyxy[2] - xyxy[0]) * (xyxy[3] - xyxy[1])
                                total_area += area
                                if c > max_conf:
                                    max_conf = c
                                fire_boxes.append(xyxy)
                        
                        if fire_boxes:
                            f_state["consecutive"] += 1
                            # Must be confirmed across at least 3 consecutive inferences
                            if f_state["consecutive"] >= 3:
                                is_new_alert = not f_state["active"]
                                cooldown_expired = (now_ts - f_state["last_shot"]) >= cooldown
                                area_doubled = f_state["last_area"] > 0 and (total_area >= f_state["last_area"] * 1.8)
                                
                                if is_new_alert or cooldown_expired or area_doubled:
                                    f_state["active"] = True
                                    f_state["last_shot"] = now_ts
                                    f_state["last_area"] = total_area
                                    
                                    best_box = fire_boxes[0]
                                    snap_url = self._save_snapshot(frame, "fire", "Fire Alert", max_conf, best_box)
                                    
                                    now = datetime.utcnow()
                                    ev = {
                                        "event_id": get_next_sequence("event_id"),
                                        "camera_id": self.camera_id,
                                        "model_id": m_id,
                                        "shop_id": shop_id,
                                        "shop_name": shop_name,
                                        "camera_name": camera.get("name"),
                                        "detection_type": "fire",
                                        "confidence": max_conf,
                                        "snapshot_path": snap_url,
                                        "track_id": None,
                                        "detected_at": now,
                                        "ended_at": None,
                                        "metadata": {"snapshot_url": snap_url, "alert_level": "critical", "area": total_area}
                                    }
                                    db["detection_events"].insert_one(ev)
                        else:
                            f_state["consecutive"] = max(0, f_state["consecutive"] - 1)
                            if f_state["consecutive"] == 0:
                                f_state["active"] = False
                        continue
                    
                    # ── OBJECT / HUMAN / WEAPON / HELMET / VEHICLE DETECTION LOGIC ──
                    detections_boxes = []
                    detections_meta = []
                    
                    for r in results:
                        for box in r.boxes:
                            cls_id = int(box.cls[0])
                            conf = float(box.conf[0])
                            label = yolo.names[cls_id]
                            xyxy = box.xyxy[0].tolist()
                            detections_boxes.append(xyxy)
                            detections_meta.append((label, conf, xyxy))
                    
                    if tracking and m_id in self.model_trackers:
                        tracker = self.model_trackers[m_id]
                        new_track_ids, lost_event_ids = tracker.update(detections_boxes)
                        
                        # Close lost tracks
                        if lost_event_ids:
                            db["detection_events"].update_many(
                                {"$or": [{"event_id": {"$in": lost_event_ids}}, {"id": {"$in": lost_event_ids}}]},
                                {"$set": {"ended_at": datetime.utcnow()}}
                            )
                        
                        # For every NEW track ID detected (e.g. Person B arrives 2s after Person A):
                        # Capture an immediate snapshot and create DB event
                        for t_id, (label, conf, xyxy) in enumerate(detections_meta):
                            track_key = f"{m_id}_{t_id}"
                            last_shot = self.last_snapshot_time.get(track_key, 0)
                            
                            is_brand_new = (t_id in new_track_ids)
                            cooldown_passed = (now_ts - last_shot) >= cooldown
                            
                            if is_brand_new or cooldown_passed:
                                self.last_snapshot_time[track_key] = now_ts
                                snap_url = self._save_snapshot(frame, m_type, label, conf, xyxy)
                                
                                now = datetime.utcnow()
                                event_id = get_next_sequence("event_id")
                                new_event = {
                                    "event_id": event_id,
                                    "camera_id": self.camera_id,
                                    "model_id": m_id,
                                    "shop_id": shop_id,
                                    "shop_name": shop_name,
                                    "camera_name": camera.get("name"),
                                    "detection_type": m_type,
                                    "confidence": conf,
                                    "snapshot_path": snap_url,
                                    "track_id": t_id,
                                    "detected_at": now,
                                    "ended_at": None,
                                    "metadata": {"snapshot_url": snap_url, "label": label, "track_id": t_id}
                                }
                                db["detection_events"].insert_one(new_event)
                                tracker.set_db_event_id(t_id, event_id)
                    else:
                        # Non-tracking mode: Cooldown per detection type
                        if detections_meta:
                            last_shot = self.last_snapshot_time.get(m_id, 0)
                            if (now_ts - last_shot) >= cooldown:
                                self.last_snapshot_time[m_id] = now_ts
                                label, conf, xyxy = detections_meta[0]
                                snap_url = self._save_snapshot(frame, m_type, label, conf, xyxy)
                                
                                now = datetime.utcnow()
                                event_id = get_next_sequence("event_id")
                                new_event = {
                                    "event_id": event_id,
                                    "camera_id": self.camera_id,
                                    "model_id": m_id,
                                    "shop_id": shop_id,
                                    "shop_name": shop_name,
                                    "camera_name": camera.get("name"),
                                    "detection_type": m_type,
                                    "confidence": conf,
                                    "snapshot_path": snap_url,
                                    "track_id": None,
                                    "detected_at": now,
                                    "ended_at": now,
                                    "metadata": {"snapshot_url": snap_url, "label": label}
                                }
                                db["detection_events"].insert_one(new_event)
                                
        except Exception as e:
            self.error_msg = str(e)
            print(f"[CameraWorker] Camera {self.camera_id} crashed: {traceback.format_exc()}", flush=True)
        finally:
            if 'cap' in locals() and cap is not None:
                cap.release()
            self.running = False
