SELECT
    registered_domain,
    'tranco' AS source_name
FROM {{ ref('mart_domain_consensus_score') }}
WHERE tranco_rank IS NOT NULL AND p_tranco IS NULL

UNION ALL

SELECT
    registered_domain,
    'majestic' AS source_name
FROM {{ ref('mart_domain_consensus_score') }}
WHERE majestic_ref_subnets IS NOT NULL AND p_majestic IS NULL

UNION ALL

SELECT
    registered_domain,
    'radar' AS source_name
FROM {{ ref('mart_domain_consensus_score') }}
WHERE radar_rank_bucket IS NOT NULL AND p_radar IS NULL

UNION ALL

SELECT
    registered_domain,
    'crux' AS source_name
FROM {{ ref('mart_domain_consensus_score') }}
WHERE crux_rank_bucket IS NOT NULL AND p_crux IS NULL

UNION ALL

SELECT
    registered_domain,
    'opr' AS source_name
FROM {{ ref('mart_domain_consensus_score') }}
WHERE openpagerank_decimal IS NOT NULL AND p_opr IS NULL
