import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from google.cloud import bigquery

from dependencies import (
    PROJECT_ID, BIGQUERY_DATASET, BIGQUERY_TABLE,
    get_bq_client, get_current_uid, get_household_id,
)
from models import DailyUsage, WeeklyHealth

logger = logging.getLogger(__name__)
router = APIRouter()

TABLE_REF = f"`{PROJECT_ID}.{BIGQUERY_DATASET}.{BIGQUERY_TABLE}`"


@router.get("/query/daily-usage", response_model=list[DailyUsage])
async def daily_usage(
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    uid: str = Depends(get_current_uid),
    bq: bigquery.Client = Depends(get_bq_client),
):
    """Daily visit count and average duration for the last N days."""
    query = f"""
        SELECT
            DATE(timestamp)  AS date,
            COUNT(*)         AS count,
            AVG(duree)       AS avg_duration
        FROM {TABLE_REF}
        WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
        GROUP BY date
        ORDER BY date ASC
    """
    logger.info(f"daily_usage query: {days} days for uid {uid}")
    rows = list(bq.query(query).result())
    return [
        DailyUsage(
            date=str(row.date),
            count=row.count,
            avg_duration_seconds=float(row.avg_duration or 0),
        )
        for row in rows
    ]


@router.get("/query/weekly-health", response_model=list[WeeklyHealth])
async def weekly_health(
    weeks: Annotated[int, Query(ge=1, le=52)] = 12,
    uid: str = Depends(get_current_uid),
    bq: bigquery.Client = Depends(get_bq_client),
):
    """Weekly event count and anomaly frequency."""
    query = f"""
        SELECT
            DATE_TRUNC(DATE(timestamp), WEEK)        AS week,
            COUNT(*)                                  AS total_events,
            COUNTIF(alerte IS NOT NULL
                    AND alerte != '')                 AS anomaly_count
        FROM {TABLE_REF}
        WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {weeks * 7} DAY)
        GROUP BY week
        ORDER BY week ASC
    """
    logger.info(f"weekly_health query: {weeks} weeks for uid {uid}")
    rows = list(bq.query(query).result())
    return [
        WeeklyHealth(
            week=str(row.week),
            total_events=row.total_events,
            anomaly_count=row.anomaly_count,
        )
        for row in rows
    ]