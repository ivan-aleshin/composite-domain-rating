WITH tranco_direction AS (
    SELECT
        'tranco' AS source_name,
        ARRAY_AGG(p_tranco ORDER BY tranco_rank ASC LIMIT 1)[OFFSET(0)] AS best_score,
        ARRAY_AGG(p_tranco ORDER BY tranco_rank DESC LIMIT 1)[OFFSET(0)] AS worst_score
    FROM {{ ref('mart_domain_consensus_score') }}
    WHERE p_tranco IS NOT NULL
),

majestic_direction AS (
    SELECT
        'majestic' AS source_name,
        ARRAY_AGG(p_majestic ORDER BY majestic_ref_subnets DESC LIMIT 1)[OFFSET(0)] AS best_score,
        ARRAY_AGG(p_majestic ORDER BY majestic_ref_subnets ASC LIMIT 1)[OFFSET(0)] AS worst_score
    FROM {{ ref('mart_domain_consensus_score') }}
    WHERE p_majestic IS NOT NULL
),

radar_direction AS (
    SELECT
        'radar' AS source_name,
        ARRAY_AGG(p_radar ORDER BY radar_rank_bucket ASC LIMIT 1)[OFFSET(0)] AS best_score,
        ARRAY_AGG(p_radar ORDER BY radar_rank_bucket DESC LIMIT 1)[OFFSET(0)] AS worst_score
    FROM {{ ref('mart_domain_consensus_score') }}
    WHERE p_radar IS NOT NULL
),

crux_direction AS (
    SELECT
        'crux' AS source_name,
        ARRAY_AGG(p_crux ORDER BY crux_rank_bucket ASC LIMIT 1)[OFFSET(0)] AS best_score,
        ARRAY_AGG(p_crux ORDER BY crux_rank_bucket DESC LIMIT 1)[OFFSET(0)] AS worst_score
    FROM {{ ref('mart_domain_consensus_score') }}
    WHERE p_crux IS NOT NULL
),

opr_direction AS (
    SELECT
        'opr' AS source_name,
        ARRAY_AGG(p_opr ORDER BY openpagerank_decimal DESC LIMIT 1)[OFFSET(0)] AS best_score,
        ARRAY_AGG(p_opr ORDER BY openpagerank_decimal ASC LIMIT 1)[OFFSET(0)] AS worst_score
    FROM {{ ref('mart_domain_consensus_score') }}
    WHERE p_opr IS NOT NULL
),

checks AS (
    SELECT * FROM tranco_direction
    UNION ALL
    SELECT * FROM majestic_direction
    UNION ALL
    SELECT * FROM radar_direction
    UNION ALL
    SELECT * FROM crux_direction
    UNION ALL
    SELECT * FROM opr_direction
)

SELECT *
FROM checks
WHERE
    best_score IS NOT NULL
    AND worst_score IS NOT NULL
    AND best_score <= worst_score
