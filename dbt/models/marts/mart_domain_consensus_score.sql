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

radar AS (
    SELECT
        registered_domain,
        rank_bucket AS radar_rank_bucket,
        buckets_seen AS radar_buckets_seen,
        p_radar,
        snapshot_date
    FROM {{ ref('int_domains_radar') }}
),

crux AS (
    SELECT
        registered_domain,
        crux_rank_bucket,
        origins_seen AS crux_origins_seen,
        p_crux,
        snapshot_date AS crux_snapshot_date
    FROM {{ ref('int_domains_crux') }}
),

opr AS (
    SELECT
        registered_domain,
        openpagerank_decimal,
        openpagerank_integer,
        openpagerank_rank,
        subdomains_seen AS opr_subdomains_seen,
        p_opr,
        snapshot_date
    FROM {{ ref('int_domains_opr') }}
),

domains AS (
    SELECT registered_domain
    FROM tranco
    UNION DISTINCT
    SELECT registered_domain
    FROM majestic
    UNION DISTINCT
    SELECT registered_domain
    FROM radar
    UNION DISTINCT
    SELECT registered_domain
    FROM crux
    UNION DISTINCT
    SELECT registered_domain
    FROM opr
),

enriched AS (
    SELECT
        domains.registered_domain,
        tranco.tranco_rank,
        tranco.p_tranco,
        majestic.majestic_ref_subnets,
        majestic.majestic_subdomains_seen,
        majestic.p_majestic,
        radar.radar_rank_bucket,
        radar.radar_buckets_seen,
        radar.p_radar,
        crux.crux_rank_bucket,
        crux.crux_origins_seen,
        crux.p_crux,
        crux.crux_snapshot_date,
        opr.openpagerank_decimal,
        opr.openpagerank_integer,
        opr.openpagerank_rank,
        opr.opr_subdomains_seen,
        opr.p_opr,
        CAST('{{ var("snapshot_date", run_started_at.strftime("%Y-%m-%d")) }}' AS DATE) AS snapshot_date,
        IF(tranco.p_tranco IS NOT NULL, 1, 0)
        + IF(majestic.p_majestic IS NOT NULL, 1, 0)
        + IF(radar.p_radar IS NOT NULL, 1, 0)
        + IF(crux.p_crux IS NOT NULL, 1, 0)
        + IF(opr.p_opr IS NOT NULL, 1, 0) AS sources_count
    FROM domains
    LEFT JOIN tranco
        ON domains.registered_domain = tranco.registered_domain
    LEFT JOIN majestic
        ON domains.registered_domain = majestic.registered_domain
    LEFT JOIN radar
        ON domains.registered_domain = radar.registered_domain
    LEFT JOIN crux
        ON domains.registered_domain = crux.registered_domain
    LEFT JOIN opr
        ON domains.registered_domain = opr.registered_domain
),

scored AS (
    SELECT
        *,
        CASE
            WHEN sources_count >= {{ var('min_sources_for_score', 3) }}
                THEN (
                    COALESCE(p_tranco, 0)
                    + COALESCE(p_majestic, 0)
                    + COALESCE(p_radar, 0)
                    + COALESCE(p_crux, 0)
                    + COALESCE(p_opr, 0)
                ) / sources_count * 100
        END AS consensus_score,
        CASE
            WHEN sources_count >= 5 THEN 'full'
            WHEN sources_count = 4 THEN 'high'
            WHEN sources_count = 3 THEN 'partial'
            ELSE 'sparse'
        END AS coverage_tier
    FROM enriched
)

SELECT
    registered_domain,
    tranco_rank,
    p_tranco,
    majestic_ref_subnets,
    majestic_subdomains_seen,
    p_majestic,
    radar_rank_bucket,
    radar_buckets_seen,
    p_radar,
    crux_rank_bucket,
    crux_origins_seen,
    p_crux,
    crux_snapshot_date,
    openpagerank_decimal,
    openpagerank_integer,
    openpagerank_rank,
    opr_subdomains_seen,
    p_opr,
    consensus_score,
    coverage_tier,
    sources_count,
    ARRAY_TO_STRING(
        ARRAY_CONCAT(
            IF(p_tranco IS NOT NULL, ['tranco'], []),
            IF(p_majestic IS NOT NULL, ['majestic'], []),
            IF(p_radar IS NOT NULL, ['radar'], []),
            IF(p_crux IS NOT NULL, ['crux'], []),
            IF(p_opr IS NOT NULL, ['opr'], [])
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
    '{{ var("methodology_version", "v0.3.0-beta") }}' AS methodology_version
FROM scored
