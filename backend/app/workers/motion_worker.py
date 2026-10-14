import threading
import time
import socket
import urllib.parse
import cv2
import numpy as np
import os
import requests

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
from datetime import datetime
from app.database import get_db, find_camera_by_id, get_next_sequence

MAX_CONSECUTIVE_FAILS = 10   # Read failures before marking as error and entering reconnect mode
RECONNECT_WAIT = 5           # Seconds between reconnect attempts during normal drops
LONG_RECONNECT_WAIT = 15     # Seconds when network/host is unreachable

def _tcp_reachable(rtsp_url: str, timeout: float = 2.0) -> bool:
    try:
        p = urllib.parse.urlparse(rtsp_url)
        host, port = p.hostname, p.port or 554
        if not host:
            return False
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False

class MotionWorker(threading.Thread):
    MOTION_THRESHOLD = 500  # Tuned for 640x360 resolution
    COOLDOWN_SECONDS = 10

    def __init__(self, camera_id: int, rtsp_url: str, shop_id: int, camera_name: str):
        super().__init__(daemon=True)
        self.camera_id = int(camera_id)
        self.rtsp_url = rtsp_url
        self.shop_id = int(shop_id) if shop_id is not None else 0
        self.camera_name = camera_name
        self.running = False
        self.error_msg = None
        self.stream_ok = None
        self._last_event_at = None
        self._open_event_id = None

    def stop(self):
        self.running = False

    def run(self):
        self.running = True
        self.error_msg = None
        self.stream_ok = None
        db = get_db()
        cap = None
        mog2 = None

        try:
            shop = db["shops"].find_one({"$or": [{"shop_id": self.shop_id}, {"id": self.shop_id}]})
            shop_name = shop.get("name") if shop else "Unknown"

            consecutive_fails = 0
            frame_count = 0

            while self.running:
                # 1. Connection establishment & Auto-Recovery Loop
                if cap is None or not cap.isOpened():
                    if not _tcp_reachable(self.rtsp_url, timeout=2.0):
                        self.error_msg = "Camera unreachable (Reconnecting...)"
                        self.stream_ok = False
                        time.sleep(LONG_RECONNECT_WAIT)
                        continue

                    # Network is reachable -> Attempt to open RTSP stream
                    if cap is not None:
                        cap.release()
                    cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
                    if not cap.isOpened():
                        self.error_msg = "Stream open failed (Reconnecting...)"
                        self.stream_ok = False
                        time.sleep(RECONNECT_WAIT)
                        continue

                    # Stream successfully opened
                    mog2 = cv2.createBackgroundSubtractorMOG2(
                        history=300, varThreshold=16, detectShadows=False
                    )
                    consecutive_fails = 0
                    frame_count = 0
                    self.error_msg = None
                    self.stream_ok = True
                    print(f"[MotionWorker] Camera {self.camera_id} ({self.camera_name}) stream connected & active.")

                # 2. Read Frame
                ret, frame = cap.read()
                frame_count += 1

                if not ret:
                    consecutive_fails += 1
                    if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                        self.error_msg = f"Stream interrupted (Reconnecting...)"
                        self.stream_ok = False
                        # Close open detection event if stream dropped
                        if self._open_event_id:
                            try:
                                db["detection_events"].update_one(
                                    {"_id": self._open_event_id, "ended_at": None},
                                    {"$set": {"ended_at": datetime.utcnow()}}
                                )
                            except Exception:
                                pass
                            self._open_event_id = None

                        cap.release()
                        cap = None
                        time.sleep(RECONNECT_WAIT)
                        continue
                    else:
                        time.sleep(1)
                        continue

                # Successful frame read -> Ensure error state is clear
                if self.error_msg is not None or not self.stream_ok:
                    self.error_msg = None
                    self.stream_ok = True
                    consecutive_fails = 0

                # 3. Process ~10 FPS (1 out of every 3 frames from 30 FPS stream)
                if frame_count % 3 != 0:
                    continue

                frame = cv2.resize(frame, (640, 360))
                fg_mask = mog2.apply(frame)
                motion_area = int(np.sum(fg_mask > 200))
                now = datetime.utcnow()

                # 4. Motion Detection Logging
                if motion_area >= self.MOTION_THRESHOLD:
                    if (self._last_event_at is None or
                            (now - self._last_event_at).total_seconds() > self.COOLDOWN_SECONDS):
                        if self._open_event_id:
                            db["detection_events"].update_one(
                                {"_id": self._open_event_id, "ended_at": None},
                                {"$set": {"ended_at": now}}
                            )

                        event_id = get_next_sequence("event_id")
                        new_ev = {
                            "event_id": event_id,
                            "camera_id": self.camera_id,
                            "model_id": None,
                            "shop_id": self.shop_id,
                            "shop_name": shop_name,
                            "camera_name": self.camera_name,
                            "detection_type": "motion",
                            "confidence": min(1.0, motion_area / 50000),
                            "detected_at": now,
                            "ended_at": None,
                            "metadata": {}
                        }
                        insert_res = db["detection_events"].insert_one(new_ev)
                        self._open_event_id = insert_res.inserted_id

                        # Check if Cloud Theft API is active for this camera
                        cam_rec = find_camera_by_id(self.camera_id)
                        if cam_rec and cam_rec.get("theft_active", False):
                            setting = db["system_settings"].find_one({"key": "cloud_trigger_url"})
                            trigger_url = setting.get("value") if setting else "http://13.48.57.153:8000/api/trigger/"

                            def _trigger_cloud():
                                try:
                                    print(f"[MotionWorker] Triggering Cloud Theft AI for {self.camera_name} to {trigger_url}")
                                    res = requests.post(trigger_url, data={
                                        "shop_name": shop_name,
                                        "camera_name": self.camera_name,
                                        "model_name": "theft"
                                    }, timeout=5)
                                    if res.status_code == 200:
                                        t_now = datetime.utcnow()
                                        db["detection_events"].insert_one({
                                            "event_id": get_next_sequence("event_id"),
                                            "camera_id": self.camera_id,
                                            "shop_id": self.shop_id,
                                            "shop_name": shop_name,
                                            "camera_name": self.camera_name,
                                            "detection_type": "cloud_trigger",
                                            "confidence": 1.0,
                                            "detected_at": t_now,
                                            "ended_at": t_now,
                                            "metadata": {}
                                        })
                                except Exception as e:
                                    print(f"[MotionWorker] Cloud Theft API trigger failed: {e}")

                            threading.Thread(target=_trigger_cloud, daemon=True).start()

                        self._last_event_at = now
                else:
                    if self._open_event_id:
                        db["detection_events"].update_one(
                            {"_id": self._open_event_id, "ended_at": None},
                            {"$set": {"ended_at": now}}
                        )
                        self._open_event_id = None

        except Exception as exc:
            self.error_msg = str(exc)
            self.stream_ok = False
            print(f"[MotionWorker] Exception in Camera {self.camera_id}: {exc}")
        finally:
            if cap is not None:
                cap.release()
            if self._open_event_id:
                try:
                    db["detection_events"].update_one(
                        {"_id": self._open_event_id, "ended_at": None},
                        {"$set": {"ended_at": datetime.utcnow()}}
                    )
                except Exception:
                    pass
            self.running = False


class MotionWorkerManager:
    def __init__(self):
        self._workers = {}
        self._lock = threading.Lock()

    def start(self, camera_id: int, rtsp_url: str, shop_id: int, camera_name: str) -> None:
        cid = int(camera_id)
        with self._lock:
            existing = self._workers.get(cid)
            if existing and existing.running:
                return
            w = MotionWorker(cid, rtsp_url, shop_id, camera_name)
            self._workers[cid] = w
            w.start()

    def stop(self, camera_id: int) -> None:
        cid = int(camera_id)
        with self._lock:
            w = self._workers.pop(cid, None)
            if w:
                w.stop()

    def get_status(self, camera_id: int) -> dict:
        cid = int(camera_id)
        with self._lock:
            w = self._workers.get(cid)
            if not w:
                return {"running": False, "error": None, "stream_ok": None}
            return {
                "running": w.running,
                "error": w.error_msg,
                "stream_ok": w.stream_ok,
            }

    def running_camera_ids(self) -> list:
        with self._lock:
            return [cid for cid, w in self._workers.items() if w.running and not w.error_msg]

    def error_camera_ids(self) -> list:
        with self._lock:
            return [cid for cid, w in self._workers.items() if w.error_msg is not None]

    def all_statuses(self) -> dict:
        with self._lock:
            return {
                cid: {
                    "running": w.running,
                    "error": w.error_msg,
                    "stream_ok": w.stream_ok,
                }
                for cid, w in self._workers.items()
            }
