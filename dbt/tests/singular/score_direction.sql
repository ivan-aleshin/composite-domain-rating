WITH ranked AS (
    SELECT
        MAX(IF(tranco_rank <= 100, consensus_score, NULL)) AS top_score,
        MIN(IF(tranco_rank > 900000, consensus_score, NULL)) AS bottom_score
    FROM {{ ref('mart_domain_consensus_score') }}
)

SELECT *
FROM ranked
WHERE
    top_score IS NOT NULL
    AND bottom_score IS NOT NULL
    AND top_score <= bottom_score
