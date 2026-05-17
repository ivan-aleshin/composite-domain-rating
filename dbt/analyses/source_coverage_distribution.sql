WITH mart AS (
    SELECT
        snapshot_date,
        coverage_tier,
        sources_count,
        ranking_sources_present,
        consensus_score
    FROM {{ ref('mart_domain_consensus_score') }}
),

totals AS (
    SELECT COUNT(*) AS total_domains
    FROM mart
)

SELECT
    mart.snapshot_date,
    mart.coverage_tier,
    mart.sources_count,
    mart.ranking_sources_present,
    COUNT(*) AS domains,
    COUNTIF(mart.consensus_score IS NOT NULL) AS scored_domains,
    ROUND(100 * SAFE_DIVIDE(COUNT(*), totals.total_domains), 4) AS domains_pct
FROM mart
CROSS JOIN totals
GROUP BY
    mart.snapshot_date,
    mart.coverage_tier,
    mart.sources_count,
    mart.ranking_sources_present,
    totals.total_domains
ORDER BY
    mart.sources_count DESC,
    domains DESC
