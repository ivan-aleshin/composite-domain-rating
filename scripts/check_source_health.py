"""Check source load health after the weekly ingestion step."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime

from google.cloud import bigquery


DEFAULT_MAXIMUM_BYTES_BILLED = 21_474_836_480
SOURCE_UPDATE_LOG_TABLE = "source_update_log"
DEFAULT_SOURCES = ("tranco", "majestic", "cloudflare", "opr")


@dataclass(frozen=True)
class SourceHealth:
    source: str
    status: str
    row_count: int | None
    age_days: int | None
    error_message: str | None

    @property
    def is_unhealthy(self) -> bool:
        return self.status in {"missing", "not_logged"} or self.row_count in {None, 0}


def quoted_table(project: str, dataset: str, table: str) -> str:
    return f"`{project}.{dataset}.{table}`"


def latest_source_health(
    client: bigquery.Client,
    project: str,
    meta_dataset: str,
    sources: tuple[str, ...],
    min_started_at: datetime | None,
    maximum_bytes_billed: int,
) -> list[SourceHealth]:
    update_window_filter = ""
    query_parameters: list[bigquery.ArrayQueryParameter | bigquery.ScalarQueryParameter] = [
        bigquery.ArrayQueryParameter("sources", "STRING", list(sources)),
    ]
    if min_started_at is not None:
        update_window_filter = "AND update_started_at >= @min_started_at"
        query_parameters.append(
            bigquery.ScalarQueryParameter("min_started_at", "TIMESTAMP", min_started_at),
        )

    query = f"""
    WITH expected_sources AS (
        SELECT source
        FROM UNNEST(@sources) AS source
    ),

    latest_updates AS (
        SELECT
            source,
            CASE status
                WHEN 'success' THEN 'fresh'
                ELSE status
            END AS status,
            row_count,
            age_days,
            error_message,
            ROW_NUMBER() OVER (
                PARTITION BY source
                ORDER BY update_completed_at DESC, update_started_at DESC
            ) AS row_number
        FROM {quoted_table(project, meta_dataset, SOURCE_UPDATE_LOG_TABLE)}
        WHERE source IN UNNEST(@sources)
            {update_window_filter}
    )

    SELECT
        expected_sources.source,
        COALESCE(latest_updates.status, 'not_logged') AS status,
        latest_updates.row_count,
        latest_updates.age_days,
        latest_updates.error_message
    FROM expected_sources
    LEFT JOIN latest_updates
        ON expected_sources.source = latest_updates.source
        AND latest_updates.row_number = 1
    ORDER BY expected_sources.source
    """
    job_config = bigquery.QueryJobConfig(
        maximum_bytes_billed=maximum_bytes_billed,
        query_parameters=query_parameters,
    )
    rows = client.query(query, job_config=job_config).result()
    return [
        SourceHealth(
            source=str(row.source),
            status=str(row.status),
            row_count=row.row_count,
            age_days=row.age_days,
            error_message=row.error_message,
        )
        for row in rows
    ]


def render_summary(results: list[SourceHealth]) -> str:
    lines = [
        "| Source | Status | Rows | Age days | Error |",
        "|---|---|---:|---:|---|",
    ]
    for result in results:
        rows = "" if result.row_count is None else str(result.row_count)
        age = "" if result.age_days is None else str(result.age_days)
        error = "" if result.error_message is None else result.error_message.replace("\n", " ")
        lines.append(f"| {result.source} | {result.status} | {rows} | {age} | {error} |")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", help="GCP project id; defaults to application default credentials project")
    parser.add_argument("--location", default="US", help="BigQuery location")
    parser.add_argument("--meta-dataset", default="meta", help="BigQuery metadata dataset")
    parser.add_argument("--sources", nargs="+", default=list(DEFAULT_SOURCES), help="Sources expected in the run")
    parser.add_argument(
        "--min-started-at",
        type=datetime.fromisoformat,
        help="Only consider source update log rows started at or after this ISO timestamp",
    )
    parser.add_argument(
        "--maximum-bytes-billed",
        type=int,
        default=DEFAULT_MAXIMUM_BYTES_BILLED,
        help="Maximum bytes billed for BigQuery SQL jobs",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = bigquery.Client(project=args.project, location=args.location)
    project = args.project or client.project
    results = latest_source_health(
        client=client,
        project=project,
        meta_dataset=args.meta_dataset,
        sources=tuple(args.sources),
        min_started_at=args.min_started_at,
        maximum_bytes_billed=args.maximum_bytes_billed,
    )
    summary = render_summary(results)
    print(summary)

    github_step_summary = os.getenv("GITHUB_STEP_SUMMARY")
    if github_step_summary:
        with open(github_step_summary, "a", encoding="utf-8") as summary_file:
            summary_file.write("## Source Health\n\n")
            summary_file.write(summary)
            summary_file.write("\n\n")

    unhealthy = [result for result in results if result.is_unhealthy]
    stale = [result for result in results if result.status == "stale"]

    if len(unhealthy) >= 2:
        print(f"::error::{len(unhealthy)} sources are missing or empty; failing weekly refresh.")
        return 2

    if unhealthy:
        print(f"::warning::{len(unhealthy)} source is missing or empty; continuing with graceful degradation.")
    if stale:
        print(f"::warning::{len(stale)} source(s) reused acceptable stale BigQuery raw data.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
