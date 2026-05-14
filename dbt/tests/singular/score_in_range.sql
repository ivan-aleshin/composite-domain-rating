SELECT
    registered_domain,
    consensus_score
FROM {{ ref('mart_domain_consensus_score') }}
WHERE
    consensus_score IS NOT NULL
    AND NOT (consensus_score BETWEEN 0 AND 100)
