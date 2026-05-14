WITH non_null_only AS (
    SELECT
        registered_domain,
        rank_bucket,
        buckets_seen,
        snapshot_date
    FROM {{ ref('stg_cloudflare__domains') }}
    WHERE rank_bucket IS NOT NULL
)

SELECT
    registered_domain,
    rank_bucket,
    buckets_seen,
    snapshot_date,
    {{ percentile_score('rank_bucket', 'asc') }} AS p_radar
FROM non_null_only
