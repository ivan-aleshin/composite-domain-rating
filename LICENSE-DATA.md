# Data License Notes

The repository code is released under the MIT License. The weekly data archives
are derived outputs built from public third-party sources. This document
summarizes how those sources are used and what is published.

This is not legal advice. Source terms can change; users should review the
linked source terms before relying on the data for commercial or production use.

## Publication Policy

The project publishes only derived aggregate output:

- `registered_domain`
- `consensus_score`
- `coverage_tier`
- source coverage summary
- lineage and methodology metadata

The project does not publish:

- raw source files;
- raw third-party ranks;
- raw Majestic `RefSubNets`;
- raw Cloudflare Radar buckets;
- source-specific percentile columns.

Raw source data is used only inside the private/local processing pipeline and
BigQuery raw tables.

## Current Ranking Sources

| Source | Current use | Public archive treatment | Notes |
|---|---|---|---|
| Tranco | Download top-1M list and normalize to registered domains | Derived score only; raw rank is not published | Research-oriented ranking; cite Tranco methodology and paper |
| Majestic Million | Download CSV and use `RefSubNets` as link breadth signal | Derived score only; raw `RefSubNets` is not published | Majestic page describes the list as Creative Commons Attribution 3.0 Unported |
| Cloudflare Radar | Download ranking bucket datasets via API | Derived score only; raw buckets are not published | Cloudflare Radar about page states Radar API/direct-download data is CC BY-NC 4.0 |

## Source References

- Tranco methodology: https://tranco-list.eu/methodology
- Tranco paper: https://tranco-list.eu/assets/tranco-ndss19.pdf
- Majestic Million: https://majestic.com/reports/majestic-million
- Cloudflare Radar about/licensing note: https://radar.cloudflare.com/about
- Cloudflare Radar datasets API: https://developers.cloudflare.com/api/resources/radar/subresources/datasets/

## Attribution

When using the public archives, please preserve the project metadata and cite the
underlying sources where appropriate:

- Tranco: Victor Le Pochat, Tom Van Goethem, Samaneh Tajalizadehkhoob, Maciej
  Korczynski, Wouter Joosen. "Tranco: A Research-Oriented Top Sites Ranking
  Hardened Against Manipulation." NDSS 2019.
- Majestic Million: Majestic, https://majestic.com/reports/majestic-million
- Cloudflare Radar: Cloudflare Radar, https://radar.cloudflare.com/

## Commercial Use

The code is MIT licensed. The derived archive is not licensed as a blanket
commercial-use dataset by this project.

Commercial or production use of the archive may be constrained by underlying
source terms, especially Cloudflare Radar's non-commercial licensing note and
any API terms that apply to source access. Users are responsible for verifying
that their intended use is compatible with those terms.

## Future Sources

New sources should not be added to the public archive process until their access
method, attribution requirements, and redistribution constraints are documented
here.

OpenPageRank is currently treated as conditional/deferred because access and
license interpretation need additional review.
