WITH source AS (
    SELECT
        registered_domain,
        tranco_rank,
        snapshot_date,
        ingested_at
    FROM {{ source('raw', 'tranco_domains') }}
),

deduplicated AS (
    SELECT
        registered_domain,
        MIN(tranco_rank) AS tranco_rank,
        MAX(snapshot_date) AS snapshot_date,
        MAX(ingested_at) AS ingested_at
    FROM source
    WHERE
        registered_domain IS NOT NULL
        AND tranco_rank IS NOT NULL
    GROUP BY registered_domain
)

SELECT
    registered_domain,
    tranco_rank,
    snapshot_date,
    ingested_at
FROM deduplicated
