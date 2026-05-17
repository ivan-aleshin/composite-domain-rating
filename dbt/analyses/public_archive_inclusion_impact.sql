WITH policy AS (
    SELECT
        registered_domain,
        ranking_sources_present,
        consensus_score,
        sources_count,
        CASE
            WHEN consensus_score IS NOT NULL THEN 'published_scored'
            WHEN sources_count >= 2 THEN 'published_sparse_multi_source'
            ELSE 'excluded_single_source'
        END AS archive_policy_bucket
    FROM {{ ref('mart_domain_consensus_score') }}
),

totals AS (
    SELECT COUNT(*) AS total_domains
    FROM policy
)

SELECT
    policy.archive_policy_bucket,
    policy.ranking_sources_present,
    COUNT(*) AS domains,
    ROUND(100 * SAFE_DIVIDE(COUNT(*), totals.total_domains), 4) AS domains_pct,
    COUNTIF(policy.consensus_score IS NOT NULL) AS scored_domains
FROM policy
CROSS JOIN totals
GROUP BY
    policy.archive_policy_bucket,
    policy.ranking_sources_present,
    totals.total_domains
ORDER BY
    policy.archive_policy_bucket ASC,
    domains DESC
