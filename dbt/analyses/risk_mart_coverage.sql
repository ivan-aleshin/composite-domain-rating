WITH risk AS (
    SELECT
        registered_domain,
        risk_sources_count,
        threat_types,
        last_threat_seen,
        threat_count,
        observed_hosts_count
    FROM {{ ref('int_domains_risk') }}
),

mart AS (
    SELECT
        registered_domain,
        sources_count,
        coverage_tier,
        consensus_score,
        consensus_score IS NOT NULL OR sources_count >= 2 AS included_in_public_archive
    FROM {{ ref('mart_domain_consensus_score') }}
),

joined AS (
    SELECT
        risk.registered_domain,
        risk.risk_sources_count,
        risk.threat_types,
        risk.last_threat_seen,
        risk.threat_count,
        risk.observed_hosts_count,
        mart.sources_count,
        mart.coverage_tier,
        mart.consensus_score,
        mart.included_in_public_archive,
        mart.registered_domain IS NOT NULL AS in_ranking_mart
    FROM risk
    LEFT JOIN mart
        ON risk.registered_domain = mart.registered_domain
)

SELECT
    risk_sources_count,
    COUNT(*) AS risk_domains,
    COUNTIF(in_ranking_mart) AS domains_in_ranking_mart,
    COUNTIF(included_in_public_archive) AS domains_in_public_archive,
    COUNTIF(risk_sources_count >= 2 AND in_ranking_mart) AS mart_domains_with_security_flag,
    COUNTIF(risk_sources_count >= 2 AND included_in_public_archive) AS public_rows_with_security_flag,
    ROUND(SAFE_DIVIDE(COUNTIF(in_ranking_mart), COUNT(*)), 6) AS pct_in_ranking_mart,
    ROUND(SAFE_DIVIDE(COUNTIF(included_in_public_archive), COUNT(*)), 6) AS pct_in_public_archive,
    ROUND(AVG(consensus_score), 4) AS avg_consensus_score,
    SUM(threat_count) AS threat_observations,
    SUM(observed_hosts_count) AS observed_hosts
FROM joined
GROUP BY risk_sources_count
ORDER BY risk_sources_count DESC
