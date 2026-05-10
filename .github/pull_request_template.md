<!--
PR title format (Conventional Commits):
  feat(staging): add stg_tranco__domains
  fix(macros): correct null handling in percentile_score
  docs: add architecture diagram to README
  refactor(intermediate): extract shared logic into macro
  test: add singular test for score direction
  chore: update dbt-utils version
  ci: add weekly_refresh workflow
-->

## What

<!-- Brief description of what this PR does -->

## Why

<!-- Why this change is needed -->

## Changes

<!-- List of significant changes -->

## Checklist

- [ ] PR title follows Conventional Commits format
- [ ] `dbt parse` passes locally
- [ ] `dbt compile` passes locally
- [ ] `sqlfluff lint` passes (or only intentional violations remain)
- [ ] New models have descriptions in `_*.yml` files
- [ ] New columns have tests where appropriate (`unique`, `not_null`)
- [ ] CHANGELOG.md updated under `[Unreleased]` if user-visible change
- [ ] No secrets, profiles.yml, or large data files committed
