WITH scored AS (
    SELECT
        registered_domain,
        consensus_score,
        sources_count,
        COALESCE(p_tranco, 0)
        + COALESCE(p_majestic, 0)
        + COALESCE(p_radar, 0)
        + COALESCE(p_crux, 0) AS source_score_sum,
        ARRAY_CONCAT(
            IF(p_tranco IS NOT NULL, [STRUCT('tranco' AS source_name, p_tranco AS score)], []),
            IF(p_majestic IS NOT NULL, [STRUCT('majestic' AS source_name, p_majestic AS score)], []),
            IF(p_radar IS NOT NULL, [STRUCT('radar' AS source_name, p_radar AS score)], []),
            IF(p_crux IS NOT NULL, [STRUCT('crux' AS source_name, p_crux AS score)], [])
        ) AS source_scores
    FROM {{ ref('mart_domain_consensus_score') }}
    WHERE
        consensus_score IS NOT NULL
        AND sources_count >= 3
),

source_influence AS (
    SELECT
        scored.registered_domain,
        source_score.source_name,
        scored.consensus_score,
        (
            (scored.source_score_sum - source_score.score)
            / (scored.sources_count - 1)
            * 100
        ) AS score_without_source,
        ABS(
            scored.consensus_score
            - (
                (scored.source_score_sum - source_score.score)
                / (scored.sources_count - 1)
                * 100
            )
        ) AS absolute_influence_points
    FROM scored
    CROSS JOIN UNNEST(scored.source_scores) AS source_score
)

SELECT
    source_name,
    COUNT(*) AS scored_domains,
    ROUND(AVG(absolute_influence_points), 6) AS avg_absolute_influence_points,
    ROUND(APPROX_QUANTILES(absolute_influence_points, 100)[OFFSET(50)], 6)
        AS median_absolute_influence_points,
    ROUND(APPROX_QUANTILES(absolute_influence_points, 100)[OFFSET(95)], 6)
        AS p95_absolute_influence_points,
    ROUND(MAX(absolute_influence_points), 6) AS max_absolute_influence_points
FROM source_influence
GROUP BY source_name
ORDER BY avg_absolute_influence_points DESC
