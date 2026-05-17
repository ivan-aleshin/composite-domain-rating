WITH source AS (
    SELECT
        origin,
        experimental.popularity.rank AS crux_rank_bucket,
        LOWER(NET.REG_DOMAIN(NET.HOST(origin))) AS registered_domain,
        PARSE_DATE('%Y%m', CAST(yyyymm AS STRING)) AS snapshot_date
    FROM {{ source('crux', 'global') }}
    WHERE
        yyyymm = CAST('{{ var("crux_yyyymm", default_crux_yyyymm()) | trim }}' AS INT64)
        AND experimental.popularity.rank IS NOT NULL
),

deduplicated AS (
    SELECT
        registered_domain,
        MIN(crux_rank_bucket) AS crux_rank_bucket,
        COUNT(DISTINCT origin) AS origins_seen,
        ANY_VALUE(snapshot_date) AS snapshot_date
    FROM source
    WHERE
        registered_domain IS NOT NULL
        AND crux_rank_bucket IS NOT NULL
    GROUP BY registered_domain
)

SELECT
    registered_domain,
    crux_rank_bucket,
    origins_seen,
    snapshot_date
FROM deduplicated
