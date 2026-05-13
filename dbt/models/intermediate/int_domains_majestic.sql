WITH non_null_only AS (
    SELECT
        registered_domain,
        ref_subnets,
        subdomains_seen,
        snapshot_date
    FROM {{ ref('stg_majestic__domains') }}
    WHERE ref_subnets IS NOT NULL
)

SELECT
    registered_domain,
    ref_subnets,
    subdomains_seen,
    snapshot_date,
    {{ percentile_score('ref_subnets', 'desc') }} AS p_majestic
FROM non_null_only
