from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class ModelBase(BaseModel):
    name: str
    type: str
    confidence_threshold: Optional[float] = 0.50
    tracking_enabled: Optional[bool] = True
    cooldown_seconds: Optional[int] = 10
    icon: Optional[str] = "fa-cube"

class ModelCreate(ModelBase):
    pass

class ModelUpdate(BaseModel):
    name: Optional[str] = None
    confidence_threshold: Optional[float] = None
    tracking_enabled: Optional[bool] = None
    cooldown_seconds: Optional[int] = None
    icon: Optional[str] = None

class ModelResponse(ModelBase):
    id: int
    version: str
    file_path: Optional[str]
    status: str
    is_default: bool
    created_at: datetime

    model_config = {"from_attributes": True}

class ModelTypeBase(BaseModel):
    name: str
    display_name: str
    icon: Optional[str] = "fa-cube"
    default_threshold: Optional[float] = 0.50
    default_tracking: Optional[bool] = True
    default_cooldown: Optional[int] = 10

class ModelTypeCreate(ModelTypeBase):
    pass

class ModelTypeResponse(ModelTypeBase):
    id: int

    model_config = {"from_attributes": True}
