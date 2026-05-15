WITH snapshot_dates AS (
    SELECT COUNT(DISTINCT snapshot_date) AS snapshot_dates_count
    FROM {{ ref('mart_domain_consensus_score') }}
)

SELECT *
FROM snapshot_dates
WHERE snapshot_dates_count != 1
