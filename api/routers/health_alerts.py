import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from google.cloud import firestore

from dependencies import get_fs_client, get_household_id
from models import HealthAlert

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health-alerts", response_model=list[HealthAlert])
async def get_health_alerts(
    acknowledged: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    household_id: str = Depends(get_household_id),
    fs: firestore.Client = Depends(get_fs_client),
):
    """Get health alerts for the current household."""
    ref = (
        fs.collection("households")
        .document(household_id)
        .collection("health_alerts")
        .order_by("timestamp", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )
    if acknowledged is not None:
        ref = ref.where("acknowledged", "==", acknowledged)

    docs = ref.get()
    return [
        HealthAlert(
            id=doc.id,
            timestamp=doc.to_dict()["timestamp"],
            cat_id=doc.to_dict().get("cat_id", ""),
            cat_name=doc.to_dict().get("cat_name", ""),
            alert_type=doc.to_dict().get("alert_type"),
            title=doc.to_dict().get("title", ""),
            description=doc.to_dict().get("description", ""),
            severity=doc.to_dict().get("severity", "warning"),
            acknowledged=doc.to_dict().get("acknowledged", False),
            source=doc.to_dict().get("source"),
        )
        for doc in docs
    ]


@router.patch("/health-alerts/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: str,
    household_id: str = Depends(get_household_id),
    fs: firestore.Client = Depends(get_fs_client),
):
    """Mark a health alert as acknowledged."""
    fs.collection("households").document(household_id).collection(
        "health_alerts"
    ).document(alert_id).update({"acknowledged": True})
    return {"status": "ok", "alert_id": alert_id}
