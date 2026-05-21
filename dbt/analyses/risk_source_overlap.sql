WITH source_domains AS (
    SELECT
        'urlhaus' AS source_name,
        registered_domain
    FROM {{ ref('stg_urlhaus__domains') }}

    UNION DISTINCT

    SELECT
        'threatfox' AS source_name,
        registered_domain
    FROM {{ ref('stg_threatfox__domains') }}

    UNION DISTINCT

    SELECT
        'phishtank' AS source_name,
        registered_domain
    FROM {{ ref('stg_phishtank__domains') }}
),

domain_sources AS (
    SELECT
        registered_domain,
        COUNT(DISTINCT source_name) AS risk_sources_count,
        ARRAY_AGG(DISTINCT source_name ORDER BY source_name) AS risk_sources_present
    FROM source_domains
    GROUP BY registered_domain
),

pairs AS (
    SELECT
        'urlhaus' AS source_a,
        'threatfox' AS source_b,
        COUNTIF(source_name = 'urlhaus') > 0 AS in_source_a,
        COUNTIF(source_name = 'threatfox') > 0 AS in_source_b
    FROM source_domains
    GROUP BY registered_domain

    UNION ALL

    SELECT
        'urlhaus' AS source_a,
        'phishtank' AS source_b,
        COUNTIF(source_name = 'urlhaus') > 0 AS in_source_a,
        COUNTIF(source_name = 'phishtank') > 0 AS in_source_b
    FROM source_domains
    GROUP BY registered_domain

    UNION ALL

    SELECT
        'threatfox' AS source_a,
        'phishtank' AS source_b,
        COUNTIF(source_name = 'threatfox') > 0 AS in_source_a,
        COUNTIF(source_name = 'phishtank') > 0 AS in_source_b
    FROM source_domains
    GROUP BY registered_domain
),

source_summary AS (
    SELECT
        'source' AS metric_type,
        source_name AS metric_name,
        COUNT(DISTINCT registered_domain) AS domains,
        NULL AS union_domains,
        NULL AS jaccard_similarity
    FROM source_domains
    GROUP BY source_name
),

source_count_summary AS (
    SELECT
        'risk_sources_count' AS metric_type,
        CAST(risk_sources_count AS STRING) AS metric_name,
        COUNT(*) AS domains,
        NULL AS union_domains,
        NULL AS jaccard_similarity
    FROM domain_sources
    GROUP BY risk_sources_count
),

pair_summary AS (
    SELECT
        'pair' AS metric_type,
        CONCAT(source_a, '_', source_b) AS metric_name,
        COUNTIF(in_source_a AND in_source_b) AS domains,
        COUNTIF(in_source_a OR in_source_b) AS union_domains,
        ROUND(SAFE_DIVIDE(COUNTIF(in_source_a AND in_source_b), COUNTIF(in_source_a OR in_source_b)), 6)
            AS jaccard_similarity
    FROM pairs
    GROUP BY
        source_a,
        source_b
)

SELECT *
FROM source_summary
UNION ALL
SELECT *
FROM source_count_summary
UNION ALL
SELECT *
FROM pair_summary
ORDER BY
    metric_type ASC,
    domains DESC
