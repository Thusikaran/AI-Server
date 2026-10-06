from app.workers.camera_worker import CameraWorker
from app.database import SessionLocal
from app.models.all_models import CameraModelAssignment, Model

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
                continue # Do not restart continuous workers in cloud mode
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
        db = SessionLocal()
        try:
            # We don't check assignments directly here anymore, just start the standard worker.
            pass
        finally:
            db.close()
            
        if camera_id not in self.workers or not self.workers[camera_id].running:
            worker = CameraWorker(camera_id=camera_id)
            worker.daemon = True
            worker.start()
            self.workers[camera_id] = worker
            
        return True

    def stop_worker(self, camera_id: int):
        db = SessionLocal()
        any_assigned = False
        try:
            assignments = db.query(CameraModelAssignment).join(Model).filter(
                CameraModelAssignment.camera_id == camera_id,
                CameraModelAssignment.is_running == True
            ).all()
            if assignments:
                any_assigned = True
        finally:
            db.close()
            
        if not any_assigned:
            if camera_id in self.workers:
                self.workers[camera_id].stop()
                self.workers[camera_id].join(timeout=2.0)
                del self.workers[camera_id]
                
        return True

    def get_status(self, camera_id: int):
        status = {"running": False, "error": None}
        if camera_id in self.workers:
            worker = self.workers[camera_id]
            status["running"] = worker.running
            status["error"] = worker.error_msg
            
        return status

manager = WorkerManager()
