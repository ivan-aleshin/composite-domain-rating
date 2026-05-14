WITH source AS (
    SELECT
        registered_domain,
        rank_bucket,
        buckets_seen,
        snapshot_date,
        ingested_at
    FROM {{ source('raw', 'cloudflare_domains') }}
),

deduplicated AS (
    SELECT
        registered_domain,
        MIN(rank_bucket) AS rank_bucket,
        MAX(buckets_seen) AS buckets_seen,
        MAX(snapshot_date) AS snapshot_date,
        MAX(ingested_at) AS ingested_at
    FROM source
    WHERE
        registered_domain IS NOT NULL
        AND rank_bucket IS NOT NULL
    GROUP BY registered_domain
)

SELECT
    registered_domain,
    rank_bucket,
    buckets_seen,
    snapshot_date,
    ingested_at
FROM deduplicated
