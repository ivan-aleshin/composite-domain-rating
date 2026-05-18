WITH non_null_only AS (
    SELECT
        registered_domain,
        openpagerank_decimal,
        openpagerank_integer,
        openpagerank_rank,
        subdomains_seen,
        snapshot_date
    FROM {{ ref('stg_opr__domains') }}
    WHERE openpagerank_decimal IS NOT NULL
)

SELECT
    registered_domain,
    openpagerank_decimal,
    openpagerank_integer,
    openpagerank_rank,
    subdomains_seen,
    snapshot_date,
    {{ percentile_score('openpagerank_decimal', 'desc') }} AS p_opr
FROM non_null_only
