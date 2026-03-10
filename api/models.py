from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class DailyUsage(BaseModel):
    date: str
    count: int
    avg_duration_seconds: float


class WeeklyHealth(BaseModel):
    week: str
    total_events: int
    anomaly_count: int


class HealthAlert(BaseModel):
    id: str
    timestamp: datetime
    cat_id: str
    cat_name: str
    alert_type: Optional[str] = None
    title: str
    description: str
    severity: str
    acknowledged: bool
    source: Optional[str] = None


class CatInfo(BaseModel):
    name: str
    weight_kg: float = 0.0


class HouseholdInfo(BaseModel):
    id: str
    join_code: str
    device_id: Optional[str] = None
    cat_names: list[str]
    cats: list[CatInfo] = []
    member_uids: list[str]
    admin_uid: Optional[str] = None


class BoxState(BaseModel):
    status: str
    last_used: Optional[datetime] = None
    fill_percent: float
    last_action: Optional[str] = None
    last_cat: Optional[str] = None
    usages_since_clean: int = 0
    last_cleaned: Optional[datetime] = None
