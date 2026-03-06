import os
import json
import logging
from datetime import datetime, timezone
import hmac

import functions_framework
from google.cloud import bigquery

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT_ID = os.environ["PROJECT_ID"]
DATASET    = os.environ["BIGQUERY_DATASET"]
TABLE      = os.environ["BIGQUERY_TABLE"]
TOKEN      = os.environ["INGEST_TOKEN"]

BQ_CLIENT  = bigquery.Client()
TABLE_REF  = f"{PROJECT_ID}.{DATASET}.{TABLE}"


def _validate_token(request) -> bool:
    token = (
        request.headers.get("X-Ingest-Token")
        or request.args.get("token")
    )
    if not token:
        return False
    # Use hmac.compare_digest to prevent timing attacks
    return hmac.compare_digest(token, TOKEN)


def _parse_payload(request) -> dict | None:
    try:
        data = request.get_json(force=True)
        if not data:
            return None
        return {
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "chat":       str(data["chat"]),
            "action":     str(data["action"]),
            "poids":      float(data.get("poids", 0)),
            "poids_chat": float(data.get("poids_chat", 0)),
            "duree":      int(data.get("duree", 0)),
            "alerte":     str(data.get("alerte", "")),
        }
    except (KeyError, ValueError, TypeError) as e:
        logger.error(f"Payload parsing error: {e}")
        return None


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
        logger.warning("Unauthorized request")
        return {"error": "Unauthorized"}, 401

    # Parse
    row = _parse_payload(request)
    if not row:
        logger.error("Invalid payload")
        return {"error": "Invalid payload"}, 400

    # Write to BigQuery
    errors = BQ_CLIENT.insert_rows_json(TABLE_REF, [row])
    if errors:
        logger.error(f"BigQuery insert errors: {errors}")
        return {"error": "BigQuery insert failed", "details": errors}, 500

    logger.info(f"Event inserted: {row['chat']} - {row['action']}")
    return {"status": "ok", "timestamp": row["timestamp"]}, 200
