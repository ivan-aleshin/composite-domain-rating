WITH pair_values AS (
    SELECT
        'tranco' AS source_a,
        'majestic' AS source_b,
        p_tranco AS source_a_score,
        p_majestic AS source_b_score
    FROM {{ ref('mart_domain_consensus_score') }}
    WHERE
        p_tranco IS NOT NULL
        AND p_majestic IS NOT NULL

    UNION ALL

    SELECT
        'tranco' AS source_a,
        'radar' AS source_b,
        p_tranco AS source_a_score,
        p_radar AS source_b_score
    FROM {{ ref('mart_domain_consensus_score') }}
    WHERE
        p_tranco IS NOT NULL
        AND p_radar IS NOT NULL

    UNION ALL

    SELECT
        'majestic' AS source_a,
        'radar' AS source_b,
        p_majestic AS source_a_score,
        p_radar AS source_b_score
    FROM {{ ref('mart_domain_consensus_score') }}
    WHERE
        p_majestic IS NOT NULL
        AND p_radar IS NOT NULL

    UNION ALL

    SELECT
        'tranco' AS source_a,
        'crux' AS source_b,
        p_tranco AS source_a_score,
        p_crux AS source_b_score
    FROM {{ ref('mart_domain_consensus_score') }}
    WHERE
        p_tranco IS NOT NULL
        AND p_crux IS NOT NULL

    UNION ALL

    SELECT
        'majestic' AS source_a,
        'crux' AS source_b,
        p_majestic AS source_a_score,
        p_crux AS source_b_score
    FROM {{ ref('mart_domain_consensus_score') }}
    WHERE
        p_majestic IS NOT NULL
        AND p_crux IS NOT NULL

    UNION ALL

    SELECT
        'radar' AS source_a,
        'crux' AS source_b,
        p_radar AS source_a_score,
        p_crux AS source_b_score
    FROM {{ ref('mart_domain_consensus_score') }}
    WHERE
        p_radar IS NOT NULL
        AND p_crux IS NOT NULL
),

ranked AS (
    SELECT
        source_a,
        source_b,
        source_a_score,
        source_b_score,
        RANK() OVER (
            PARTITION BY source_a, source_b
            ORDER BY source_a_score
        ) AS source_a_rank,
        RANK() OVER (
            PARTITION BY source_a, source_b
            ORDER BY source_b_score
        ) AS source_b_rank
    FROM pair_values
)

SELECT
    source_a,
    source_b,
    COUNT(*) AS overlap_domains,
    ROUND(CORR(source_a_score, source_b_score), 6) AS pearson_on_percentiles,
    ROUND(CORR(source_a_rank, source_b_rank), 6) AS spearman_correlation,
    ROUND(AVG(ABS(source_a_score - source_b_score)), 6) AS avg_abs_percentile_gap
FROM ranked
GROUP BY
    source_a,
    source_b
ORDER BY
    overlap_domains DESC
