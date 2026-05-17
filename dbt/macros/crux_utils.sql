{% macro default_crux_yyyymm() %}
    {#
        CrUX publishes the previous calendar month on the second Tuesday.
        Use a conservative default: previous month on/after day 14, otherwise
        two months back. Override with `--vars '{crux_yyyymm: YYYYMM}'`.
    #}
    {%- set target_year = run_started_at.year -%}
    {%- if run_started_at.day >= 14 -%}
        {%- set target_month = run_started_at.month - 1 -%}
    {%- else -%}
        {%- set target_month = run_started_at.month - 2 -%}
    {%- endif -%}
    {%- if target_month <= 0 -%}
        {%- set target_year = target_year - 1 -%}
        {%- set target_month = target_month + 12 -%}
    {%- endif -%}
    {{ "%04d%02d"|format(target_year, target_month) }}
{% endmacro %}
