"""Domain normalization helpers for ingestion scripts."""

from __future__ import annotations

import ipaddress
import re
from functools import lru_cache

import idna
import tldextract


CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
RESERVED_SUFFIXES = {"local", "localhost", "internal", "test"}


@lru_cache(maxsize=1)
def _extractor() -> tldextract.TLDExtract:
    """Return a reproducible PSL extractor that does not fetch live updates."""
    return tldextract.TLDExtract(
        suffix_list_urls=None,
        fallback_to_snapshot=True,
    )


def normalize_registered_domain(raw_domain: str) -> str | None:
    """Normalize a hostname/domain to its registered domain in ASCII form.

    Returns None for values that are not valid public registered domains.
    """
    if raw_domain is None:
        return None

    domain = raw_domain.strip().strip(".").lower()
    if not domain or len(domain) > 253 or CONTROL_CHARS.search(domain):
        return None

    try:
        ipaddress.ip_address(domain)
        return None
    except ValueError:
        pass

    try:
        ascii_domain = idna.encode(domain, uts46=True).decode("ascii")
    except idna.IDNAError:
        return None

    extracted = _extractor()(ascii_domain)
    suffix = extracted.suffix.lower()
    if not extracted.domain or not suffix or suffix in RESERVED_SUFFIXES:
        return None

    registered_domain = extracted.registered_domain
    if not registered_domain or len(registered_domain) > 253:
        return None

    return registered_domain
