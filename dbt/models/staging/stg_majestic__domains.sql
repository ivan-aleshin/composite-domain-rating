WITH source AS (
    SELECT
        registered_domain,
        ref_subnets,
        subdomains_seen,
        snapshot_date,
        ingested_at
    FROM {{ source('raw', 'majestic_domains') }}
),

deduplicated AS (
    SELECT
        registered_domain,
        MAX(ref_subnets) AS ref_subnets,
        SUM(subdomains_seen) AS subdomains_seen,
        MAX(snapshot_date) AS snapshot_date,
        MAX(ingested_at) AS ingested_at
    FROM source
    WHERE
        registered_domain IS NOT NULL
        AND ref_subnets IS NOT NULL
    GROUP BY registered_domain
)

SELECT
    registered_domain,
    ref_subnets,
    subdomains_seen,
    snapshot_date,
    ingested_at
FROM deduplicated
