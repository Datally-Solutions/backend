import os
from functools import lru_cache

from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.cloud import bigquery, firestore
from firebase_admin import auth as firebase_auth

PROJECT_ID       = os.environ["PROJECT_ID"]
BIGQUERY_DATASET = os.environ["BIGQUERY_DATASET"]
BIGQUERY_TABLE   = os.environ["BIGQUERY_TABLE"]
FIRESTORE_DB     = os.environ.get("FIRESTORE_DATABASE", "(default)")

security = HTTPBearer()


@lru_cache
def get_bq_client() -> bigquery.Client:
    return bigquery.Client()


@lru_cache
def get_fs_client() -> firestore.Client:
    return firestore.Client(database=FIRESTORE_DB)


async def get_current_uid(
    credentials: HTTPAuthorizationCredentials = Security(security),
) -> str:
    """Validate Firebase ID token and return the user's UID."""
    try:
        decoded = firebase_auth.verify_id_token(credentials.credentials)
        return decoded["uid"]
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


async def get_household_id(
    uid: str = Depends(get_current_uid),
    fs: firestore.Client = Depends(get_fs_client),
) -> str:
    """Get the household ID for the current user."""
    snap = (
        fs.collection("households")
        .where("member_uids", "array_contains", uid)
        .limit(1)
        .get()
    )
    if not snap:
        raise HTTPException(status_code=404, detail="No household found for this user")
    return snap[0].id