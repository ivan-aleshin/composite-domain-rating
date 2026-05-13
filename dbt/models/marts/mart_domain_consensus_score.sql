{{ config(
    partition_by={
        "field": "snapshot_date",
        "data_type": "date"
    },
    cluster_by=["registered_domain"]
) }}

WITH tranco AS (
    SELECT
        registered_domain,
        tranco_rank,
        p_tranco,
        snapshot_date
    FROM {{ ref('int_domains_tranco') }}
)

SELECT
    registered_domain,
    tranco_rank,
    p_tranco,
    p_tranco * 100 AS consensus_score,
    'sparse' AS coverage_tier,
    1 AS sources_count,
    'tranco' AS ranking_sources_present,
    CAST(NULL AS STRING) AS tld_category,
    CAST(NULL AS BOOL) AS is_spam_prone_tld,
    FALSE AS security_flags_observed,
    0 AS risk_sources_count,
    ARRAY<STRING>[] AS threat_types,
    CAST(NULL AS DATE) AS last_threat_seen,
    snapshot_date,
    '{{ var("methodology_version", "v1.0.0") }}' AS methodology_version
FROM tranco
