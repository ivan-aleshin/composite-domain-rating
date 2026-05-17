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
            SELECT source_score.source_name
            FROM UNNEST(source_scores) AS source_score
            ORDER BY source_score.score DESC, source_score.source_name ASC
            LIMIT 1
        ) AS strongest_source,
        (
            SELECT source_score.score
            FROM UNNEST(source_scores) AS source_score
            ORDER BY source_score.score DESC, source_score.source_name ASC
            LIMIT 1
        ) AS strongest_source_score,
        (
            SELECT source_score.source_name
            FROM UNNEST(source_scores) AS source_score
            ORDER BY source_score.score ASC, source_score.source_name ASC
            LIMIT 1
        ) AS weakest_source,
        (
            SELECT source_score.score
            FROM UNNEST(source_scores) AS source_score
            ORDER BY source_score.score ASC, source_score.source_name ASC
            LIMIT 1
        ) AS weakest_source_score,
        ARRAY_TO_STRING(
            ARRAY(
                SELECT CONCAT(source_score.source_name, ':', CAST(ROUND(source_score.score, 4) AS STRING))
                FROM UNNEST(source_scores) AS source_score
                ORDER BY source_score.score DESC, source_score.source_name ASC
            ),
            ','
        ) AS source_score_summary
    FROM domain_scores
)

SELECT
    registered_domain,
    sources_count,
    strongest_source,
    weakest_source,
    source_score_summary,
    ROUND(consensus_score, 4) AS consensus_score,
    ROUND(source_score_range, 6) AS source_score_range,
    ROUND(strongest_source_score, 6) AS strongest_source_score,
    ROUND(weakest_source_score, 6) AS weakest_source_score
FROM agreement
ORDER BY
    source_score_range DESC,
    consensus_score DESC
