import os
import json
import logging
from datetime import datetime, timezone
import hmac
import functions_framework
from google.cloud import bigquery, firestore
import google.cloud.logging

logging.basicConfig(level=logging.INFO)
google.cloud.logging.Client().setup_logging()

PROJECT_ID   = os.environ["PROJECT_ID"]
DATASET      = os.environ["BIGQUERY_DATASET"]
TABLE        = os.environ["BIGQUERY_TABLE"]
TOKEN        = os.environ["INGEST_TOKEN"]

BQ_CLIENT  = bigquery.Client()
FS_CLIENT  = firestore.Client(database="cat-litter-monitor-firestore")
TABLE_REF  = f"{PROJECT_ID}.{DATASET}.{TABLE}"


def _get_or_create_household(device_id: str) -> str:
    """Find household by device_id or create it. Returns household_id."""
    households = FS_CLIENT.collection("households")

    # Look for existing household with this device
    snap = households.where("device_id", "==", device_id).limit(1).get()
    if snap:
        return snap[0].id

    # Create new household for this device
    import random, string
    join_code = ''.join(random.choices('ABCDEFGHJKLMNPQRSTUVWXYZ23456789', k=6))
    ref = households.add({
        "device_id":    device_id,
        "join_code":    join_code,
        "member_uids":  [],
        "admin_uid":    "",
        "cat_names":    [],
        "created_at":   firestore.SERVER_TIMESTAMP,
    })
    logging.info(f"Created new household {ref[1].id} for device {device_id}")
    return ref[1].id


def _validate_token(request) -> bool:
    token = (
        request.headers.get("X-Ingest-Token")
        or request.args.get("token")
    )
    if not token:
        return False
    return hmac.compare_digest(token, TOKEN)


def _parse_payload(request) -> dict | None:
    try:
        data = request.get_json(force=True)
        if not data:
            return None
        return {
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "device_id":  str(data.get("device_id", "default-device")),
            "chat":       str(data["chat"]),
            "action":     str(data["action"]),
            "poids":      float(data.get("poids", 0)),
            "poids_chat": float(data.get("poids_chat", 0)),
            "duree":      int(data.get("duree", 0)),
            "alerte":     str(data.get("alerte", "")) or None,
        }
    except (KeyError, ValueError, TypeError) as e:
        logging.error(f"Payload parsing error: {e}")
        return None


def _write_to_firestore(row: dict, household_id: str):
    """Write live state + recent event to Firestore."""
    household_ref = FS_CLIENT.collection("households").document(household_id)
    ts = datetime.fromisoformat(row["timestamp"])

    # ── 1. Update live box_state ──────────────────────────────────────────────
    alerte = row.get("alerte")
    fill_percent = min(max(row["poids"] / 500.0, 0.0), 1.0)  # rough estimate

    if alerte:
        status = "needs_clean"
    elif row["action"].lower() in ("simple visite", "simple visit"):
        status = "clean"
    else:
        status = "clean"

    household_ref.collection("box_state").document("current").set({
        "status":            status,
        "last_used":         ts,
        "fill_percent":      fill_percent,
        "last_action":       row["action"],
        "last_cat":          row["chat"],
        "usages_since_clean": firestore.Increment(1),
        "last_cleaned":      firestore.SERVER_TIMESTAMP,  # only on first set
    }, merge=True)

    # On cleaning action, reset counter
    if "nettoy" in row["action"].lower() or "clean" in row["action"].lower():
        household_ref.collection("box_state").document("current").update({
            "usages_since_clean": 0,
            "last_cleaned": ts,
            "status": "clean",
        })

    # ── 2. Add event to recent events ─────────────────────────────────────────
    household_ref.collection("events").add({
        "timestamp":        ts,
        "cat_id":           row["chat"].lower(),
        "cat_name":         row["chat"],
        "action":           row["action"],
        "weight_delta_g":   row["poids"],
        "cat_weight_kg":    row["poids_chat"],
        "duration_seconds": row["duree"],
        "anomaly":          bool(alerte),
    })

    # ── 3. Write health alert if needed ───────────────────────────────────────
    if alerte:
        household_ref.collection("health_alerts").add({
            "timestamp":    ts,
            "cat_id":       row["chat"].lower(),
            "title":        f"Alerte détectée — {row['chat']}",
            "description":  alerte,
            "severity":     "warning",
            "acknowledged": False,
        })
        logging.info(f"Health alert written for {row['chat']}: {alerte}")


@functions_framework.http
def ingest_litter_event(request):
    # CORS preflight
    if request.method == "OPTIONS":
        return ("", 204, {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "X-Ingest-Token, Content-Type",
        })

    # Auth
    if not _validate_token(request):
        logging.warning("Unauthorized request")
        return {"error": "Unauthorized"}, 401

    # Parse
    row = _parse_payload(request)
    if not row:
        logging.error("Invalid payload")
        return {"error": "Invalid payload"}, 400

    # ── Resolve household from device_id ─────────────────────────────────────
    household_id = _get_or_create_household(row["device_id"])

    # ── Write to BigQuery ─────────────────────────────────────────────────────
    errors = BQ_CLIENT.insert_rows_json(TABLE_REF, [row])
    if errors:
        logging.error(f"BigQuery insert errors: {errors}")
        return {"error": "BigQuery insert failed", "details": errors}, 500

    # ── Write to Firestore ────────────────────────────────────────────────────
    try:
        _write_to_firestore(row, household_id)
    except Exception as e:
        logging.error(f"Firestore write error: {e}")

    logging.info(f"Event inserted: {row['chat']} - {row['action']} (household: {household_id})")
    return {"status": "ok", "timestamp": row["timestamp"], "household_id": household_id}, 200