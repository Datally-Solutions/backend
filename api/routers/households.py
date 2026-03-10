import logging
from fastapi import APIRouter, Depends, HTTPException
from google.cloud import firestore

from dependencies import get_fs_client, get_current_uid, get_household_id
from models import HouseholdInfo, BoxState, CatInfo

logger = logging.getLogger(__name__)
router = APIRouter()


# helper to parse cats from Firestore data
def _parse_cats(data: dict) -> tuple[list, list[str]]:
    raw_cats = data.get("cats", [])
    cats = [CatInfo(name=c["name"], weight_kg=c.get("weight_kg", 0)) for c in raw_cats]
    # fallback to cat_names if cats is empty
    cat_names = [c.name for c in cats] if cats else data.get("cat_names", [])
    return cats, cat_names


@router.get("/households/me", response_model=HouseholdInfo)
async def get_my_household(
    household_id: str = Depends(get_household_id),
    fs: firestore.Client = Depends(get_fs_client),
):
    """Get the current user's household info."""
    doc = fs.collection("households").document(household_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Household not found")
    data = doc.to_dict()
    cats, cat_names = _parse_cats(data)
    return HouseholdInfo(
        id=doc.id,
        join_code=data.get("join_code", ""),
        device_id=data.get("device_id"),
        cat_names=cat_names,
        cats=cats,
        member_uids=data.get("member_uids", []),
        admin_uid=data.get("admin_uid"),
    )


@router.post("/households/join", response_model=HouseholdInfo)
async def join_household(
    join_code: str,
    uid: str = Depends(get_current_uid),
    fs: firestore.Client = Depends(get_fs_client),
):
    """Join a household by join code."""
    snap = (
        fs.collection("households")
        .where("join_code", "==", join_code.upper())
        .limit(1)
        .get()
    )
    if not snap:
        raise HTTPException(status_code=404, detail="Household not found")

    doc = snap[0]
    fs.collection("households").document(doc.id).update(
        {"member_uids": firestore.ArrayUnion([uid])}
    )
    data = doc.to_dict()
    cats, cat_names = _parse_cats(data)
    return HouseholdInfo(
        id=doc.id,
        join_code=data.get("join_code", ""),
        device_id=data.get("device_id"),
        cat_names=cat_names,
        cats=cats,
        member_uids=list(set(data.get("member_uids", []) + [uid])),
        admin_uid=data.get("admin_uid"),
    )


@router.get("/households/me/box-state", response_model=BoxState)
async def get_box_state(
    household_id: str = Depends(get_household_id),
    fs: firestore.Client = Depends(get_fs_client),
):
    """Get the current live box state."""
    doc = (
        fs.collection("households")
        .document(household_id)
        .collection("box_state")
        .document("current")
        .get()
    )
    if not doc.exists:
        return BoxState(status="unknown", fill_percent=0)
    data = doc.to_dict()
    return BoxState(
        status=data.get("status", "unknown"),
        last_used=data.get("last_used"),
        fill_percent=data.get("fill_percent", 0),
        last_action=data.get("last_action"),
        last_cat=data.get("last_cat"),
        usages_since_clean=data.get("usages_since_clean", 0),
        last_cleaned=data.get("last_cleaned"),
    )
