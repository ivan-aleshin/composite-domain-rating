SELECT
    registered_domain,
    consensus_score,
    coverage_tier,
    sources_count
FROM {{ ref('mart_domain_consensus_score') }}
WHERE
    (
        coverage_tier = 'sparse'
        AND consensus_score IS NOT NULL
    )
    OR (
        coverage_tier != 'sparse'
        AND consensus_score IS NULL
    )
    OR (
        sources_count < {{ var('min_sources_for_score', 3) }}
        AND coverage_tier != 'sparse'
    )
    OR (
        sources_count >= {{ var('min_sources_for_score', 3) }}
        AND coverage_tier = 'sparse'
    )
