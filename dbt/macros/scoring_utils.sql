{#
    Returns inverted percentile so that "best" (per direction) gets ~1.0.

    direction='asc'  for "lower is better" sources (Tranco rank, Radar bucket, CrUX bucket)
    direction='desc' for "higher is better" sources (Majestic ref_subnets, OPR rank)

    Important: PERCENT_RANK() returns 0 for the first row in ORDER BY.
    Without inversion, best domains would get score close to 0 instead of 1.
    The `1 - PERCENT_RANK()` inversion makes semantics match intuition:
    higher score = better domain.

    Contract: filter NULLs in the input column upstream before calling this
    macro. BigQuery includes NULL rows in the window, which shifts percentiles
    for non-NULL rows even if the output is later CASE'd back to NULL.

    Usage in models:
        WITH non_null_only AS (
            SELECT registered_domain, tranco_rank
            FROM {{ ref('stg_tranco__domains') }}
            WHERE tranco_rank IS NOT NULL
        )

        SELECT
            registered_domain,
            {{ percentile_score('tranco_rank', 'asc') }} AS p_tranco
        FROM non_null_only
#}

{% macro percentile_score(column, direction='asc') %}
    1 - PERCENT_RANK() OVER (ORDER BY {{ column }} {{ direction }})
{% endmacro %}
