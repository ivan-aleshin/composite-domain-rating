{#
    Adds invocation context to every query as a SQL comment.
    Visible in BigQuery query history for debugging and audit.
    Configured via query-comment in dbt_project.yml.
#}

{% macro query_comment(node) %}
    {%- set comment_dict = {} -%}
    {%- do comment_dict.update(
        app='dbt',
        dbt_version=dbt_version,
        profile_name=target.profile_name,
        target_name=target.name,
        invocation_id=invocation_id,
        methodology_version=var('methodology_version', 'unknown'),
    ) -%}
    {%- if node is not none -%}
        {%- do comment_dict.update(
            node_id=node.unique_id,
            materialized=node.config.materialized,
        ) -%}
    {%- endif -%}
    {{ tojson(comment_dict) }}
{% endmacro %}
