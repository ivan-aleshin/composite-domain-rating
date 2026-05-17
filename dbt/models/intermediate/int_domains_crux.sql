WITH non_null_only AS (
    SELECT
        registered_domain,
        crux_rank_bucket,
        origins_seen,
        snapshot_date
    FROM {{ ref('stg_crux__origins') }}
    WHERE crux_rank_bucket IS NOT NULL
)

SELECT
    registered_domain,
    crux_rank_bucket,
    origins_seen,
    snapshot_date,
    {{ percentile_score('crux_rank_bucket', 'asc') }} AS p_crux
FROM non_null_only
