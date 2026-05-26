WITH known_platform_domains AS (
    SELECT platform_domain
    FROM UNNEST([
        'amazonaws.com',
        'appspot.com',
        'azurewebsites.net',
        'base44.app',
        'blogspot.com',
        'cloudfront.net',
        'dropbox.com',
        'duckdns.org',
        'firebaseapp.com',
        'github.io',
        'google.com',
        'googleapis.com',
        'herokuapp.com',
        'netlify.app',
        'pages.dev',
        'r2.dev',
        'secureserver.net',
        'trycloudflare.com',
        'vercel.app',
        'web.app',
        'wixsite.com',
        'workers.dev'
    ]) AS platform_domain
),

risk AS (
    SELECT
        registered_domain,
        risk_sources_count,
        threat_types,
        first_threat_seen,
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
        consensus_score
    FROM {{ ref('mart_domain_consensus_score') }}
),

candidates AS (
    SELECT
        risk.registered_domain,
        risk.risk_sources_count,
        risk.threat_types,
        risk.first_threat_seen,
        risk.last_threat_seen,
        risk.threat_count,
        risk.observed_hosts_count,
        mart.sources_count,
        mart.coverage_tier,
        mart.consensus_score,
        known_platform_domains.platform_domain IS NOT NULL AS in_known_platform_seed,
        risk.observed_hosts_count >= 10 AS has_many_observed_hosts,
        risk.threat_count >= 20 AS has_many_observations
    FROM risk
    LEFT JOIN mart
        ON risk.registered_domain = mart.registered_domain
    LEFT JOIN known_platform_domains
        ON risk.registered_domain = known_platform_domains.platform_domain
    WHERE risk.risk_sources_count >= 2
)

SELECT
    registered_domain,
    risk_sources_count,
    threat_types,
    first_threat_seen,
    last_threat_seen,
    threat_count,
    observed_hosts_count,
    sources_count AS ranking_sources_count,
    coverage_tier,
    consensus_score,
    in_known_platform_seed,
    has_many_observed_hosts,
    has_many_observations,
    ARRAY_TO_STRING(
        ARRAY_CONCAT(
            IF(in_known_platform_seed, ['known_platform_seed'], []),
            IF(has_many_observed_hosts, ['many_observed_hosts'], []),
            IF(has_many_observations, ['many_observations'], [])
        ),
        ','
    ) AS candidate_reasons
FROM candidates
WHERE
    in_known_platform_seed
    OR has_many_observed_hosts
    OR has_many_observations
ORDER BY
    risk_sources_count DESC,
    in_known_platform_seed DESC,
    observed_hosts_count DESC,
    threat_count DESC,
    registered_domain ASC
