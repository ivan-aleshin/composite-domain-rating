SELECT
    registered_domain,
    sources_count,
    IF(p_tranco IS NOT NULL, 1, 0)
    + IF(p_majestic IS NOT NULL, 1, 0)
    + IF(p_radar IS NOT NULL, 1, 0) AS expected_sources_count
FROM {{ ref('mart_domain_consensus_score') }}
WHERE sources_count != (
    IF(p_tranco IS NOT NULL, 1, 0)
    + IF(p_majestic IS NOT NULL, 1, 0)
    + IF(p_radar IS NOT NULL, 1, 0)
)
