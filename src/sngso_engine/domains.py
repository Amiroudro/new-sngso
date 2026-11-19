"""Canonical DNS identity and source-domain scope helpers.

These functions are shared by orchestration and persistence so both trust
boundaries apply identical host normalization and allowlist rules.
"""
from __future__ import annotations

import ipaddress
from typing import Any


def canonical_dns_name(value: Any) -> str | None:
    """Return a lowercase IDNA ASCII DNS name, or None for invalid input."""
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    try:
        canonical = value.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if not canonical or len(canonical) > 253:
        return None
    try:
        ipaddress.ip_address(canonical)
    except ValueError:
        pass
    else:
        return None
    labels = canonical.split(".")
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in label)
        for label in labels
    ):
        return None
    return canonical


def valid_domain_allowlist(domains: Any) -> bool:
    """Require a non-empty list of unique, already-canonical DNS names."""
    if not isinstance(domains, list) or not domains:
        return False
    normalized = [canonical_dns_name(domain) for domain in domains]
    return (
        all(value is not None for value in normalized)
        and all(domain == canonical for domain, canonical in zip(domains, normalized))
        and len(set(normalized)) == len(normalized)
    )


def host_in_domain_allowlist(host: str, domains: Any) -> bool:
    """Check exact host or subdomain membership using canonical DNS names."""
    if not isinstance(domains, (list, tuple)) or not domains:
        return False
    normalized_host = canonical_dns_name(host)
    if not normalized_host:
        return False
    normalized_domains = [canonical_dns_name(domain) for domain in domains]
    if any(domain is None for domain in normalized_domains):
        return False
    return any(
        normalized_host == domain or normalized_host.endswith("." + domain)
        for domain in normalized_domains
    )
