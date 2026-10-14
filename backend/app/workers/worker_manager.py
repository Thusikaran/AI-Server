from app.workers.camera_worker import CameraWorker
from app.database import get_db

class WorkerManager:
    def __init__(self):
        self.workers = {} 
        import threading
        self.watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self.watchdog_thread.start()

    def _watchdog_loop(self):
        import time
        from app.utils.settings import get_server_mode
        while True:
            time.sleep(60)
            if get_server_mode() == "cloud":
                continue
            try:
                all_cams = set(self.workers.keys())
                for cam_id in list(all_cams):
                    w = self.workers.get(cam_id)
                    if w and not w.running:
                        print(f"[WATCHDOG] Detected dead worker for Camera {cam_id}. Restarting...", flush=True)
                        del self.workers[cam_id]
                        self.start_worker(cam_id)
            except Exception as e:
                print(f"[WATCHDOG] Error: {e}", flush=True)

    def start_worker(self, camera_id: int):
        cid = int(camera_id)
        if cid not in self.workers or not self.workers[cid].running:
            worker = CameraWorker(camera_id=cid)
            worker.daemon = True
            worker.start()
            self.workers[cid] = worker
        return True

    def stop_worker(self, camera_id: int):
        cid = int(camera_id)
        db = get_db()
        any_assigned = db["camera_model_assignments"].count_documents({
            "camera_id": cid,
            "is_running": True
        }) > 0
            
        if not any_assigned:
            if cid in self.workers:
                self.workers[cid].stop()
                self.workers[cid].join(timeout=2.0)
                del self.workers[cid]
                
        return True

    def get_status(self, camera_id: int):
        cid = int(camera_id)
        status = {"running": False, "error": None}
        if cid in self.workers:
            worker = self.workers[cid]
            status["running"] = worker.running
            status["error"] = worker.error_msg
            
        return status

manager = WorkerManager()
