WITH source_counts AS (
    SELECT
        'tranco' AS source_name,
        COUNTIF(p_tranco IS NOT NULL) AS populated_rows
    FROM {{ ref('mart_domain_consensus_score') }}
    UNION ALL
    SELECT
        'majestic' AS source_name,
        COUNTIF(p_majestic IS NOT NULL) AS populated_rows
    FROM {{ ref('mart_domain_consensus_score') }}
    UNION ALL
    SELECT
        'radar' AS source_name,
        COUNTIF(p_radar IS NOT NULL) AS populated_rows
    FROM {{ ref('mart_domain_consensus_score') }}
    UNION ALL
    SELECT
        'crux' AS source_name,
        COUNTIF(p_crux IS NOT NULL) AS populated_rows
    FROM {{ ref('mart_domain_consensus_score') }}
)

SELECT *
FROM source_counts
WHERE populated_rows = 0
