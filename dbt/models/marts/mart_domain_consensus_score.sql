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
),

majestic AS (
    SELECT
        registered_domain,
        ref_subnets AS majestic_ref_subnets,
        subdomains_seen AS majestic_subdomains_seen,
        p_majestic,
        snapshot_date
    FROM {{ ref('int_domains_majestic') }}
),

domains AS (
    SELECT registered_domain
    FROM tranco
    UNION DISTINCT
    SELECT registered_domain
    FROM majestic
),

enriched AS (
    SELECT
        domains.registered_domain,
        tranco.tranco_rank,
        tranco.p_tranco,
        majestic.majestic_ref_subnets,
        majestic.majestic_subdomains_seen,
        majestic.p_majestic,
        COALESCE(tranco.snapshot_date, majestic.snapshot_date) AS snapshot_date,
        IF(tranco.p_tranco IS NOT NULL, 1, 0)
        + IF(majestic.p_majestic IS NOT NULL, 1, 0) AS sources_count
    FROM domains
    LEFT JOIN tranco
        ON domains.registered_domain = tranco.registered_domain
    LEFT JOIN majestic
        ON domains.registered_domain = majestic.registered_domain
)

SELECT
    registered_domain,
    tranco_rank,
    p_tranco,
    majestic_ref_subnets,
    majestic_subdomains_seen,
    p_majestic,
    (
        COALESCE(p_tranco, 0)
        + COALESCE(p_majestic, 0)
    ) / sources_count * 100 AS consensus_score,
    'sparse' AS coverage_tier,
    sources_count,
    ARRAY_TO_STRING(
        ARRAY_CONCAT(
            IF(p_tranco IS NOT NULL, ['tranco'], []),
            IF(p_majestic IS NOT NULL, ['majestic'], [])
        ),
        ','
    ) AS ranking_sources_present,
    CAST(NULL AS STRING) AS tld_category,
    CAST(NULL AS BOOL) AS is_spam_prone_tld,
    FALSE AS security_flags_observed,
    0 AS risk_sources_count,
    ARRAY<STRING>[] AS threat_types,
    CAST(NULL AS DATE) AS last_threat_seen,
    snapshot_date,
    '{{ var("methodology_version", "v1.0.0") }}' AS methodology_version
FROM enriched
