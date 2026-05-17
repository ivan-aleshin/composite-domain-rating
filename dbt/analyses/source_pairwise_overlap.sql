WITH source_flags AS (
    SELECT
        registered_domain,
        p_tranco IS NOT NULL AS has_tranco,
        p_majestic IS NOT NULL AS has_majestic,
        p_radar IS NOT NULL AS has_radar
    FROM {{ ref('mart_domain_consensus_score') }}
),

pairs AS (
    SELECT
        'tranco' AS source_a,
        'majestic' AS source_b,
        has_tranco AS in_source_a,
        has_majestic AS in_source_b
    FROM source_flags

    UNION ALL

    SELECT
        'tranco' AS source_a,
        'radar' AS source_b,
        has_tranco AS in_source_a,
        has_radar AS in_source_b
    FROM source_flags

    UNION ALL

    SELECT
        'majestic' AS source_a,
        'radar' AS source_b,
        has_majestic AS in_source_a,
        has_radar AS in_source_b
    FROM source_flags
)

SELECT
    source_a,
    source_b,
    COUNTIF(in_source_a) AS source_a_domains,
    COUNTIF(in_source_b) AS source_b_domains,
    COUNTIF(in_source_a AND in_source_b) AS overlap_domains,
    COUNTIF(in_source_a OR in_source_b) AS union_domains,
    ROUND(SAFE_DIVIDE(COUNTIF(in_source_a AND in_source_b), COUNTIF(in_source_a OR in_source_b)), 6)
        AS jaccard_similarity,
    ROUND(SAFE_DIVIDE(COUNTIF(in_source_a AND in_source_b), COUNTIF(in_source_a)), 6)
        AS pct_source_a_covered_by_b,
    ROUND(SAFE_DIVIDE(COUNTIF(in_source_a AND in_source_b), COUNTIF(in_source_b)), 6)
        AS pct_source_b_covered_by_a
FROM pairs
GROUP BY
    source_a,
    source_b
ORDER BY
    overlap_domains DESC
