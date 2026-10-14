import os
from typing import Optional, Dict, Any, List
from pymongo import MongoClient, ReturnDocument
from bson import ObjectId
from dotenv import load_dotenv

# Ensure environment variables are loaded
load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "cctv_surveillance")

# Centralized MongoDB Client with connection pooling
client = MongoClient(
    MONGODB_URI,
    maxPoolSize=50,
    minPoolSize=5,
    serverSelectionTimeoutMS=10000,
    connectTimeoutMS=10000,
    retryWrites=True,
    w="majority"
)

db = client[MONGODB_DB_NAME]

def get_mongo_client():
    return client

def get_db():
    """Dependency for FastAPI routes to access the MongoDB database instance."""
    return db

def get_next_sequence(sequence_name: str) -> int:
    """
    Thread-safe auto-increment sequence generator matching Main Server's 'counters' collection.
    """
    counter = db["counters"].find_one_and_update(
        {"_id": sequence_name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER
    )
    return counter["seq"]

def serialize_doc(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Converts MongoDB ObjectId to string and formats document for JSON responses.
    """
    if doc is None:
        return None
    d = dict(doc)
    if "_id" in d:
        d["id"] = str(d["_id"])
    return d

def serialize_docs(docs) -> List[Dict[str, Any]]:
    return [serialize_doc(d) for d in docs if d is not None]

# --- Camera & Shop Helper Functions for Embedded Architecture ---

def find_all_cameras(filter_active_only: bool = False) -> List[Dict[str, Any]]:
    """
    Extracts all cameras across all shops from the embedded shops.cameras array.
    """
    pipeline = [
        {"$unwind": "$cameras"},
        {
            "$project": {
                "_id": 0,
                "shop_id": "$shop_id",
                "shop_name": "$name",
                "camera": "$cameras"
            }
        }
    ]
    cameras = []
    for item in db["shops"].aggregate(pipeline):
        cam = item.get("camera", {})
        cam["shop_id"] = item.get("shop_id")
        cam["shop_name"] = item.get("shop_name")
        # Ensure id and camera_id are synchronized
        cam_id = cam.get("camera_id") or cam.get("id")
        cam["id"] = cam_id
        cam["camera_id"] = cam_id
        if filter_active_only and not cam.get("is_active", True):
            continue
        cameras.append(cam)
    return cameras

def find_camera_by_id(camera_id: int) -> Optional[Dict[str, Any]]:
    """
    Finds a specific camera across shops by its camera_id.
    """
    cam_id_int = int(camera_id)
    shop = db["shops"].find_one(
        {"cameras": {"$elemMatch": {"$or": [{"camera_id": cam_id_int}, {"id": cam_id_int}]}}},
        {"name": 1, "shop_id": 1, "cameras.$": 1}
    )
    if not shop or not shop.get("cameras"):
        return None
    
    cam = shop["cameras"][0]
    cam["shop_id"] = shop.get("shop_id")
    cam["shop_name"] = shop.get("name")
    cam["id"] = cam.get("camera_id") or cam.get("id") or cam_id_int
    cam["camera_id"] = cam["id"]
    return cam

def update_camera_by_id(camera_id: int, updates: Dict[str, Any]) -> bool:
    """
    Updates specific fields of a camera inside its parent shop document.
    """
    cam_id_int = int(camera_id)
    set_fields = {f"cameras.$.{k}": v for k, v in updates.items()}
    res = db["shops"].update_one(
        {"cameras": {"$elemMatch": {"$or": [{"camera_id": cam_id_int}, {"id": cam_id_int}]}}},
        {"$set": set_fields}
    )
    return res.modified_count > 0 or res.matched_count > 0
