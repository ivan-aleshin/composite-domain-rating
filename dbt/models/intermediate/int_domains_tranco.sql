WITH non_null_only AS (
    SELECT
        registered_domain,
        tranco_rank,
        snapshot_date
    FROM {{ ref('stg_tranco__domains') }}
    WHERE tranco_rank IS NOT NULL
)

SELECT
    registered_domain,
    tranco_rank,
    snapshot_date,
    {{ percentile_score('tranco_rank', 'asc') }} AS p_tranco
FROM non_null_only
