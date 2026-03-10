import os
import logging
from datetime import datetime, timedelta, timezone
import hmac
import functions_framework
from google.cloud import bigquery, firestore
import google.cloud.logging

logging.basicConfig(level=logging.INFO)
google.cloud.logging.Client().setup_logging()

PROJECT_ID = os.environ["PROJECT_ID"]
DATASET = os.environ["BIGQUERY_DATASET"]
TABLE = os.environ["BIGQUERY_TABLE"]
TOKEN = os.environ["INGEST_TOKEN"]

BQ_CLIENT = bigquery.Client()
FS_CLIENT = firestore.Client(database="cat-litter-monitor-firestore")
TABLE_REF = f"{PROJECT_ID}.{DATASET}.{TABLE}"


def _get_household(device_id: str) -> tuple[str, dict] | None:
    """Find household by device_id. Returns (household_id, data) or None."""
    snap = (
        FS_CLIENT.collection("households")
        .where("device_id", "==", device_id)
        .limit(1)
        .get()
    )
    if snap:
        return snap[0].id, snap[0].to_dict()
    return None


def _identify_cat(entry_weight_kg: float, cats: list[dict]) -> dict | None:
    """Identify cat by entry weight. Returns cat dict or None if unknown."""
    if not cats:
        return None

    best_match = None
    best_diff = float("inf")

    for cat in cats:
        cat_weight = cat.get("weight_kg", 0)
        if cat_weight <= 0:
            continue
        # Allow ±30% tolerance around cat weight
        tolerance = cat_weight * 0.30
        diff = abs(entry_weight_kg - cat_weight)
        if diff <= tolerance and diff < best_diff:
            best_diff = diff
            best_match = cat

    return best_match


def _classify_action(
    cat: dict | None, exit_weight_delta_g: float, duration_seconds: int
) -> tuple[str, str | None]:
    """
    Classify action based on weight delta and duration.
    Returns (action, alerte).
    """
    # Use cat-specific thresholds or fallback to defaults
    if cat:
        cat_weight = cat.get("weight_kg", 0)
        # Scale thresholds proportionally to cat weight
        # Base reference: 4kg cat → visite_max=15g, pipi_max=50g
        scale = cat_weight / 4.0 if cat_weight > 0 else 1.0
        visite_max = 15.0 * scale
        pipi_max = 50.0 * scale
    else:
        visite_max = 15.0
        pipi_max = 55.0

    alerte = None

    # Negative weight = scale noise, treat as visit
    if exit_weight_delta_g < 0:
        return "Simple visite", None

    if exit_weight_delta_g < visite_max:
        action = "Simple visite"
        if duration_seconds > 90:
            alerte = "*Alerte :* Grattage long sans résultat."

    elif exit_weight_delta_g < pipi_max:
        if duration_seconds > 120:
            action = "Petit Pipi 🟡"
            alerte = "*Vigilance :* Long pour un petit résultat."
        else:
            action = "Pipi 🟡"

    else:
        # Heavy deposit — distinguish caca vs gros pipi by duration
        if duration_seconds > 90:
            action = "Caca 🟤"
        else:
            action = "Gros Pipi 🟡"

    # Extra long session alert
    if duration_seconds > 240 and not alerte:
        alerte = "*Attention :* Session extrêmement longue (+4 min)."

    return action, alerte


def _validate_token(request) -> bool:
    token = request.headers.get("X-Ingest-Token") or request.args.get("token")
    if not token:
        return False
    return hmac.compare_digest(token, TOKEN)


def _parse_payload(request) -> dict | None:
    """Parse new raw firmware payload."""
    try:
        data = request.get_json(force=True)
        if not data:
            return None
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "device_id": str(data.get("device_id", "default-device")),
            "entry_weight_kg": float(data.get("entry_weight_kg", 0)),
            "exit_weight_delta_g": float(data.get("exit_weight_delta_g", 0)),
            "duration_seconds": int(data.get("duration_seconds", 0)),
        }
    except (KeyError, ValueError, TypeError) as e:
        logging.error(f"Payload parsing error: {e}")
        return None


def _write_to_firestore(
    row: dict, household_id: str, cat: dict | None, action: str, alerte: str | None
):
    """Write live state + recent event to Firestore."""
    household_ref = FS_CLIENT.collection("households").document(household_id)
    ts = datetime.fromisoformat(row["timestamp"])
    box_ref = household_ref.collection("box_state").document("current")

    cat_name = cat["name"] if cat else "Inconnu"
    cat_id = cat_name.lower()

    # ── 1. Update live box_state ──────────────────────────────────────────────
    is_cleaning = "nettoy" in action.lower() or "clean" in action.lower()

    if is_cleaning:
        status = "clean"
    elif alerte:
        status = "needs_clean"
    else:
        status = "clean"

    box_ref.set(
        {
            "status": status,
            "last_used": ts,
            "last_action": action,
            "last_cat": cat_name,
            "usages_since_clean": firestore.Increment(1),
            "fill_percent": firestore.Increment(row["exit_weight_delta_g"] / 500.0),
        },
        merge=True,
    )

    # Initialize last_cleaned only if document is new
    doc = box_ref.get()
    if not doc.exists or "last_cleaned" not in doc.to_dict():
        box_ref.update({"last_cleaned": ts})

    # On cleaning action, reset counters
    if is_cleaning:
        box_ref.update(
            {
                "usages_since_clean": 0,
                "last_cleaned": ts,
                "status": "clean",
                "fill_percent": 0.0,
            }
        )

    # ── 2. Add event ──────────────────────────────────────────────────────────
    household_ref.collection("events").add(
        {
            "timestamp": ts,
            "cat_id": cat_id,
            "cat_name": cat_name,
            "action": action,
            "weight_delta_g": row["exit_weight_delta_g"],
            "cat_weight_kg": cat["weight_kg"] if cat else 0,
            "duration_seconds": row["duration_seconds"],
            "anomaly": bool(alerte),
            "expire_at": ts + timedelta(days=90),
        }
    )

    # ── 3. Write health alert if needed ───────────────────────────────────────
    if alerte:
        household_ref.collection("health_alerts").add(
            {
                "timestamp": ts,
                "cat_id": cat_id,
                "cat_name": cat_name,
                "alert_type": "anomaly",
                "title": f"Alerte détectée — {cat_name}",
                "description": alerte,
                "severity": "warning",
                "acknowledged": False,
                "source": "litter_ingest",
                "expire_at": datetime.now(timezone.utc) + timedelta(days=365),
            }
        )
        logging.info(f"Health alert written for {cat_name}: {alerte}")


def _write_to_bigquery(row: dict, cat: dict | None, action: str, alerte: str | None):
    """Write classified event to BigQuery."""
    cat_name = cat["name"] if cat else "Inconnu"
    bq_row = {
        "timestamp": row["timestamp"],
        "device_id": row["device_id"],
        "chat": cat_name,
        "action": action,
        "poids": row["exit_weight_delta_g"],
        "poids_chat": row["entry_weight_kg"],
        "duree": row["duration_seconds"],
        "alerte": alerte or "",
    }
    errors = BQ_CLIENT.insert_rows_json(TABLE_REF, [bq_row])
    if errors:
        logging.error(f"BigQuery insert errors: {errors}")
        raise Exception(f"BigQuery insert failed: {errors}")


@functions_framework.http
def ingest_litter_event(request):
    # CORS preflight
    if request.method == "OPTIONS":
        return (
            "",
            204,
            {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "X-Ingest-Token, Content-Type",
            },
        )

    # Auth
    if not _validate_token(request):
        logging.warning("Unauthorized request")
        return {"error": "Unauthorized"}, 401

    # Parse
    row = _parse_payload(request)
    if not row:
        logging.error("Invalid payload")
        return {"error": "Invalid payload"}, 400

    # ── Resolve household + cat profiles ─────────────────────────────────────
    result = _get_household(row["device_id"])
    if not result:
        logging.warning(f"No household found for device {row['device_id']}")
        return {"error": "Device not registered"}, 404

    household_id, household_data = result
    cats = household_data.get("cats", [])

    # ── Classify ──────────────────────────────────────────────────────────────
    cat = _identify_cat(row["entry_weight_kg"], cats)
    action, alerte = _classify_action(
        cat, row["exit_weight_delta_g"], row["duration_seconds"]
    )

    logging.info(
        f"Classified: cat={cat['name'] if cat else 'Inconnu'} "
        f"action={action} delta={row['exit_weight_delta_g']}g "
        f"duration={row['duration_seconds']}s"
    )

    # ── Write to BigQuery ─────────────────────────────────────────────────────
    try:
        _write_to_bigquery(row, cat, action, alerte)
    except Exception as e:
        logging.error(f"BigQuery write error: {e}")
        return {"error": str(e)}, 500

    # ── Write to Firestore ────────────────────────────────────────────────────
    try:
        _write_to_firestore(row, household_id, cat, action, alerte)
    except Exception as e:
        logging.error(f"Firestore write error: {e}")

    logging.info(
        f"Event inserted: {cat['name'] if cat else 'Inconnu'} - {action} "
        f"(household: {household_id})"
    )
    return {
        "status": "ok",
        "timestamp": row["timestamp"],
        "household_id": household_id,
        "cat": cat["name"] if cat else "Inconnu",
        "action": action,
    }, 200
