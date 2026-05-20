SELECT
    registered_domain,
    security_flags_observed,
    risk_sources_count
FROM {{ ref('mart_domain_consensus_score') }}
WHERE
    security_flags_observed
    AND risk_sources_count < 2
