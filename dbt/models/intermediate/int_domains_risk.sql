WITH urlhaus AS (
    SELECT
        registered_domain,
        'urlhaus' AS source_name,
        threat_type,
        first_seen,
        last_seen,
        threat_count,
        observed_hosts_count
    FROM {{ ref('stg_urlhaus__domains') }}
),

aggregated AS (
    SELECT
        registered_domain,
        COUNT(DISTINCT source_name) AS risk_sources_count,
        ARRAY_AGG(DISTINCT threat_type IGNORE NULLS ORDER BY threat_type) AS threat_types,
        MIN(first_seen) AS first_threat_seen,
        MAX(last_seen) AS last_threat_seen,
        SUM(threat_count) AS threat_count,
        SUM(observed_hosts_count) AS observed_hosts_count
    FROM urlhaus
    WHERE registered_domain IS NOT NULL
    GROUP BY registered_domain
)

SELECT
    registered_domain,
    risk_sources_count,
    threat_types,
    first_threat_seen,
    last_threat_seen,
    threat_count,
    observed_hosts_count
FROM aggregated
