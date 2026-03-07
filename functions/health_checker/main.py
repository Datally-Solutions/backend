import os
import json
import logging
from datetime import datetime, timezone, timedelta
import google.cloud.logging

import functions_framework
from google.cloud import bigquery, firestore
import firebase_admin
from firebase_admin import messaging

logging.basicConfig(level=logging.INFO)
google.cloud.logging.Client().setup_logging()

PROJECT_ID = os.environ["PROJECT_ID"]
DATASET    = os.environ["BIGQUERY_DATASET"]
TABLE      = os.environ["BIGQUERY_TABLE"]

BQ_CLIENT = bigquery.Client()
FS_CLIENT = firestore.Client(database=os.environ.get("FIRESTORE_DATABASE", "(default)"))

if not firebase_admin._apps:
    firebase_admin.initialize_app()

# ─── Thresholds ───────────────────────────────────────────────────────────────
NO_PEE_HOURS        = 24
NO_POOP_HOURS       = 48
WEIGHT_CHANGE_PCT   = 10.0   # % change over last 7 days vs previous 7 days

# Actions considered as pee or poop
PEE_ACTIONS  = ["pipi", "gros pipi", "petit pipi"]
POOP_ACTIONS = ["caca"]


# ─── BigQuery helpers ─────────────────────────────────────────────────────────

def _hours_since_last_action(cat_name: str, action_keywords: list[str]) -> float | None:
    """Returns hours since last matching action for a cat, or None if never."""
    keywords_filter = " OR ".join(
        [f"LOWER(action) LIKE '%{kw}%'" for kw in action_keywords]
    )
    query = f"""
        SELECT MAX(timestamp) as last_action
        FROM `{PROJECT_ID}.{DATASET}.{TABLE}`
        WHERE LOWER(chat) = LOWER(@cat_name)
          AND ({keywords_filter})
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("cat_name", "STRING", cat_name),
    ])
    rows = list(BQ_CLIENT.query(query, job_config=job_config).result())
    if not rows or rows[0].last_action is None:
        return None
    last = rows[0].last_action.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last).total_seconds() / 3600


def _get_avg_weight(cat_name: str, days_ago_start: int, days_ago_end: int) -> float | None:
    """Returns average cat weight over a time window."""
    query = f"""
        SELECT AVG(poids_chat) as avg_weight
        FROM `{PROJECT_ID}.{DATASET}.{TABLE}`
        WHERE LOWER(chat) = LOWER(@cat_name)
          AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days_ago_start} DAY)
          AND timestamp < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days_ago_end} DAY)
          AND poids_chat > 0
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("cat_name", "STRING", cat_name),
    ])
    rows = list(BQ_CLIENT.query(query, job_config=job_config).result())
    if not rows or rows[0].avg_weight is None:
        return None
    return float(rows[0].avg_weight)


def _get_cats() -> list[dict]:
    """Get all unique cats and their household_id from Firestore."""
    cats = []
    households = FS_CLIENT.collection("households").stream()
    for household in households:
        data = household.to_dict()
        household_id = household.id
        cat_names = data.get("cat_names", [])
        # If no cat_names registered yet, get from BigQuery
        if not cat_names:
            query = f"""
                SELECT DISTINCT chat
                FROM `{PROJECT_ID}.{DATASET}.{TABLE}`
            """
            rows = list(BQ_CLIENT.query(query).result())
            cat_names = [row.chat for row in rows]
        for cat_name in cat_names:
            cats.append({
                "name": cat_name,
                "household_id": household_id,
                "member_uids": data.get("member_uids", []),
            })
    return cats


# ─── Alert helpers ────────────────────────────────────────────────────────────

def _alert_already_sent_today(household_id: str, cat_name: str, alert_type: str) -> bool:
    """Avoid sending the same alert twice in one day."""
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    snap = (
        FS_CLIENT.collection("households")
        .document(household_id)
        .collection("health_alerts")
        .where("cat_id", "==", cat_name.lower())
        .where("alert_type", "==", alert_type)
        .where("timestamp", ">=", today_start)
        .limit(1)
        .get()
    )
    return len(snap) > 0


def _write_alert(household_id: str, cat_name: str, alert_type: str,
                 title: str, description: str, severity: str):
    FS_CLIENT.collection("households") \
        .document(household_id) \
        .collection("health_alerts") \
        .add({
            "timestamp":    datetime.now(timezone.utc),
            "cat_id":       cat_name.lower(),
            "cat_name":     cat_name,
            "alert_type":   alert_type,
            "title":        title,
            "description":  description,
            "severity":     severity,
            "acknowledged": False,
            "source":       "health_checker",
        })
    logging.info(f"Alert written: [{severity}] {title}")


def _send_fcm(member_uids: list[str], title: str, description: str, severity: str):
    """Send FCM push to all household members."""
    for uid in member_uids:
        user_doc = FS_CLIENT.collection("users").document(uid).get()
        if not user_doc.exists:
            continue
        token = user_doc.to_dict().get("fcm_token")
        if not token:
            continue
        try:
            messaging.send(messaging.Message(
                token=token,
                notification=messaging.Notification(
                    title=title,
                    body=description[:120] + "…" if len(description) > 120 else description,
                ),
                data={
                    "type":     "health_alert",
                    "severity": severity,
                },
                android=messaging.AndroidConfig(
                    priority="high",
                    notification=messaging.AndroidNotification(
                        channel_id="health_alerts",
                        priority="max" if severity == "critical" else "high",
                    ),
                ),
                apns=messaging.APNSConfig(
                    payload=messaging.APNSPayload(
                        aps=messaging.Aps(sound="default", badge=1)
                    )
                ),
            ))
            logging.info(f"FCM sent to uid {uid}")
        except Exception as e:
            logging.error(f"FCM error for uid {uid}: {e}")


def _process_alert(cat: dict, alert_type: str, title: str, description: str, severity: str):
    """Write alert + send FCM if not already sent today."""
    household_id = cat["household_id"]
    cat_name     = cat["name"]

    if _alert_already_sent_today(household_id, cat_name, alert_type):
        logging.info(f"Alert [{alert_type}] already sent today for {cat_name}, skipping.")
        return

    _write_alert(household_id, cat_name, alert_type, title, description, severity)
    _send_fcm(cat["member_uids"], title, description, severity)


# ─── Health rules ─────────────────────────────────────────────────────────────

def check_no_pee(cat: dict):
    hours = _hours_since_last_action(cat["name"], PEE_ACTIONS)
    if hours is None:
        logging.info(f"{cat['name']}: no pee data found, skipping.")
        return
    logging.info(f"{cat['name']}: last pee {hours:.1f}h ago")
    if hours >= NO_PEE_HOURS:
        _process_alert(
            cat,
            alert_type="no_pee_24h",
            title=f"{cat['name']} n'a pas fait pipi",
            description=(
                f"{cat['name']} n'a pas utilisé la litière pour uriner depuis "
                f"{int(hours)} heures. Cela peut indiquer une infection urinaire, "
                f"une obstruction ou de la déshydratation. Consultez un vétérinaire "
                f"si cela persiste."
            ),
            severity="critical",
        )


def check_no_poop(cat: dict):
    hours = _hours_since_last_action(cat["name"], POOP_ACTIONS)
    if hours is None:
        logging.info(f"{cat['name']}: no poop data found, skipping.")
        return
    logging.info(f"{cat['name']}: last poop {hours:.1f}h ago")
    if hours >= NO_POOP_HOURS:
        _process_alert(
            cat,
            alert_type="no_poop_48h",
            title=f"{cat['name']} n'a pas fait caca",
            description=(
                f"{cat['name']} n'a pas déféqué depuis {int(hours)} heures. "
                f"Une constipation prolongée peut être sérieuse. Vérifiez son "
                f"alimentation, son hydratation et consultez un vétérinaire si "
                f"nécessaire."
            ),
            severity="critical",
        )


def check_weight_change(cat: dict):
    """Compare average weight last 7 days vs previous 7 days."""
    recent_weight = _get_avg_weight(cat["name"], days_ago_start=0, days_ago_end=7)
    previous_weight = _get_avg_weight(cat["name"], days_ago_start=7, days_ago_end=14)

    if recent_weight is None or previous_weight is None:
        logging.info(f"{cat['name']}: insufficient weight data, skipping.")
        return

    change_pct = abs(recent_weight - previous_weight) / previous_weight * 100
    direction  = "perdu" if recent_weight < previous_weight else "pris"

    logging.info(
        f"{cat['name']}: weight {previous_weight:.2f}kg → {recent_weight:.2f}kg "
        f"({change_pct:.1f}% change)"
    )

    if change_pct >= WEIGHT_CHANGE_PCT:
        _process_alert(
            cat,
            alert_type="weight_change_10pct",
            title=f"Changement de poids — {cat['name']}",
            description=(
                f"{cat['name']} a {direction} {change_pct:.1f}% de son poids "
                f"sur les 7 derniers jours "
                f"({previous_weight:.2f} kg → {recent_weight:.2f} kg). "
                f"Un changement de poids significatif peut indiquer un problème "
                f"de santé. Consultez votre vétérinaire."
            ),
            severity="warning",
        )


# ─── Main handler ─────────────────────────────────────────────────────────────

@functions_framework.http
def health_checker(request):
    """Triggered by Cloud Scheduler via HTTP."""
    logging.info("Health checker started")

    cats = _get_cats()
    if not cats:
        logging.warning("No cats found in Firestore households")
        return {"status": "ok", "message": "No cats found"}, 200

    results = []
    for cat in cats:
        logging.info(f"Checking health for {cat['name']} (household: {cat['household_id']})")
        try:
            check_no_pee(cat)
            check_no_poop(cat)
            check_weight_change(cat)
            results.append({"cat": cat["name"], "status": "checked"})
        except Exception as e:
            logging.error(f"Error checking {cat['name']}: {e}")
            results.append({"cat": cat["name"], "status": "error", "error": str(e)})

    logging.info(f"Health checker done. Results: {results}")
    return {"status": "ok", "results": results}, 200