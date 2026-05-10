{#
    Returns inverted percentile so that "best" (per direction) gets ~1.0.

    direction='asc'  for "lower is better" sources (Tranco rank, Radar bucket, CrUX bucket)
    direction='desc' for "higher is better" sources (Majestic ref_subnets, OPR rank)

    Important: PERCENT_RANK() returns 0 for the first row in ORDER BY.
    Without inversion, best domains would get score ≈ 0 instead of ≈ 1.
    The `1 - PERCENT_RANK()` inversion makes semantics match intuition:
    higher score = better domain.

    Usage in models:
        SELECT
            registered_domain,
            {{ percentile_score('tranco_rank', 'asc') }} AS p_tranco
        FROM {{ ref('stg_tranco__domains') }}
        WHERE tranco_rank IS NOT NULL  -- filter NULLs upstream
#}

{% macro percentile_score(column, direction='asc') %}
    CASE
        WHEN {{ column }} IS NULL THEN NULL
        ELSE 1 - PERCENT_RANK() OVER (ORDER BY {{ column }} {{ direction }})
    END
{% endmacro %}
