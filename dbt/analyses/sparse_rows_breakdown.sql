WITH sparse_rows AS (
    SELECT
        sources_count,
        ranking_sources_present,
        ARRAY_TO_STRING(
            ARRAY_CONCAT(
                IF(p_tranco IS NULL, ['tranco'], []),
                IF(p_majestic IS NULL, ['majestic'], []),
                IF(p_radar IS NULL, ['radar'], [])
            ),
            ','
        ) AS missing_sources
    FROM {{ ref('mart_domain_consensus_score') }}
    WHERE coverage_tier = 'sparse'
),

totals AS (
    SELECT COUNT(*) AS total_sparse_domains
    FROM sparse_rows
)

SELECT
    sparse_rows.sources_count,
    sparse_rows.ranking_sources_present,
    sparse_rows.missing_sources,
    COUNT(*) AS domains,
    ROUND(100 * SAFE_DIVIDE(COUNT(*), totals.total_sparse_domains), 4) AS sparse_domains_pct
FROM sparse_rows
CROSS JOIN totals
GROUP BY
    sparse_rows.sources_count,
    sparse_rows.ranking_sources_present,
    sparse_rows.missing_sources,
    totals.total_sparse_domains
ORDER BY
    sparse_rows.sources_count DESC,
    domains DESC
