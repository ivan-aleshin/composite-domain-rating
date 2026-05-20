WITH source AS (
    SELECT
        registered_domain,
        threat_type,
        first_seen,
        last_seen,
        threat_count,
        observed_hosts_count,
        snapshot_date,
        ingested_at
    FROM {{ source('raw', 'threatfox_domains') }}
),

deduplicated AS (
    SELECT
        registered_domain,
        threat_type,
        MIN(first_seen) AS first_seen,
        MAX(last_seen) AS last_seen,
        SUM(threat_count) AS threat_count,
        MAX(observed_hosts_count) AS observed_hosts_count,
        MAX(snapshot_date) AS snapshot_date,
        MAX(ingested_at) AS ingested_at
    FROM source
    WHERE
        registered_domain IS NOT NULL
        AND threat_type IS NOT NULL
        AND first_seen IS NOT NULL
        AND last_seen IS NOT NULL
    GROUP BY
        registered_domain,
        threat_type
)

SELECT
    registered_domain,
    threat_type,
    first_seen,
    last_seen,
    threat_count,
    observed_hosts_count,
    snapshot_date,
    ingested_at
FROM deduplicated
