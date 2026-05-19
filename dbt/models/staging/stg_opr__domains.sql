WITH source AS (
    SELECT
        registered_domain,
        openpagerank_decimal,
        openpagerank_integer,
        openpagerank_rank,
        snapshot_date,
        ingested_at
    FROM {{ source('raw', 'opr_domains') }}
),

deduplicated AS (
    SELECT
        registered_domain,
        MAX(openpagerank_decimal) AS openpagerank_decimal,
        MAX(openpagerank_integer) AS openpagerank_integer,
        MIN(openpagerank_rank) AS openpagerank_rank,
        COUNT(*) AS subdomains_seen,
        MAX(snapshot_date) AS snapshot_date,
        MAX(ingested_at) AS ingested_at
    FROM source
    WHERE
        registered_domain IS NOT NULL
        AND (
            openpagerank_decimal IS NOT NULL
            OR openpagerank_integer IS NOT NULL
            OR openpagerank_rank IS NOT NULL
        )
    GROUP BY registered_domain
)

SELECT
    registered_domain,
    openpagerank_decimal,
    openpagerank_integer,
    openpagerank_rank,
    subdomains_seen,
    snapshot_date,
    ingested_at
FROM deduplicated
