WITH domain_scores AS (
    SELECT
        registered_domain,
        sources_count,
        consensus_score,
        ARRAY_CONCAT(
            IF(p_tranco IS NOT NULL, [STRUCT('tranco' AS source_name, p_tranco AS score)], []),
            IF(p_majestic IS NOT NULL, [STRUCT('majestic' AS source_name, p_majestic AS score)], []),
            IF(p_radar IS NOT NULL, [STRUCT('radar' AS source_name, p_radar AS score)], [])
        ) AS source_scores
    FROM {{ ref('mart_domain_consensus_score') }}
    WHERE sources_count >= 2
),

agreement AS (
    SELECT
        registered_domain,
        sources_count,
        consensus_score,
        (
            SELECT MAX(source_score.score) - MIN(source_score.score)
            FROM UNNEST(source_scores) AS source_score
        ) AS source_score_range,
        (
            SELECT STDDEV_POP(source_score.score)
            FROM UNNEST(source_scores) AS source_score
        ) AS source_score_stddev
    FROM domain_scores
)

SELECT
    sources_count,
    -- Exploratory diagnostic buckets only, not part of the public scoring methodology.
    -- Thresholds are expressed in percentile-score range units on the 0..1 scale.
    CASE
        WHEN source_score_range < 0.10 THEN 'very_high_agreement'
        WHEN source_score_range < 0.25 THEN 'high_agreement'
        WHEN source_score_range < 0.50 THEN 'mixed_agreement'
        ELSE 'low_agreement'
    END AS agreement_bucket,
    COUNT(*) AS domains,
    ROUND(AVG(consensus_score), 4) AS avg_consensus_score,
    ROUND(AVG(source_score_range), 6) AS avg_source_score_range,
    ROUND(AVG(source_score_stddev), 6) AS avg_source_score_stddev
FROM agreement
GROUP BY
    sources_count,
    agreement_bucket
ORDER BY
    sources_count DESC,
    avg_source_score_range ASC
