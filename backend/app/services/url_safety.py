"""URL handling for user-supplied job links.

RoleMatch never fetches an arbitrary user-supplied URL. A pasted link is only *parsed*: if it is a posting on a
supported source (Greenhouse or Lever), the matching adapter calls that source's fixed, documented API host with
identifiers that pass strict validation. Any other URL is stored for reference only and the user pastes the
description text. `assert_public_http_url` is an additional guard used before any outbound request built from
user input, rejecting non-HTTP(S) schemes, credentials in URLs, and hosts that resolve to private, loopback,
link-local, multicast or reserved addresses (cloud metadata endpoints included).
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

IDENT_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
JOB_ID_RX = re.compile(r"^[A-Za-z0-9-]{1,64}$")


class UnsafeURL(ValueError):
    pass


@dataclass(frozen=True)
class SupportedPosting:
    kind: str  # greenhouse | lever
    identifier: str  # board token / site
    job_id: str
    region: str = "us"


def parse_supported_posting(url: str) -> SupportedPosting | None:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower()
    segs = [p for p in parts.path.split("/") if p]
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io") and len(segs) >= 3 and segs[1] == "jobs":
        token, job_id = segs[0], segs[2]
        if IDENT_RX.match(token) and job_id.isdigit():
            return SupportedPosting("greenhouse", token, job_id)
    if host in ("jobs.lever.co", "jobs.eu.lever.co") and len(segs) >= 2:
        site, job_id = segs[0], segs[1]
        if IDENT_RX.match(site) and JOB_ID_RX.match(job_id):
            return SupportedPosting("lever", site, job_id, "eu" if ".eu." in host else "us")
    return None


def canonicalize_url(url: str | None) -> str | None:
    """Lower-case scheme/host, drop query/fragment tracking noise and trailing slashes."""
    if not url:
        return None
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not p.hostname:
        return None
    path = re.sub(r"/+$", "", p.path) or "/"
    host = p.hostname.lower()
    if host.startswith("www."):
        host = host[4:]
    # Greenhouse embeds and board URLs carry the job id in ?gh_jid=; keep that one parameter.
    query = ""
    if "gh_jid=" in (p.query or ""):
        m = re.search(r"gh_jid=(\d+)", p.query)
        query = f"gh_jid={m.group(1)}" if m else ""
    return urlunsplit(("https", host, path, query, ""))


def _is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return not (
        addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_reserved
        or addr.is_unspecified or (isinstance(addr, ipaddress.IPv4Address) and addr in ipaddress.ip_network("100.64.0.0/10"))
    )


def assert_public_http_url(url: str, resolver=socket.getaddrinfo) -> str:
    p = urlsplit(url)
    if p.scheme not in ("http", "https"):
        raise UnsafeURL("only http(s) URLs are allowed")
    if p.username or p.password:
        raise UnsafeURL("credentials in URLs are not allowed")
    if not p.hostname:
        raise UnsafeURL("missing host")
    try:
        infos = resolver(p.hostname, p.port or (443 if p.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURL("host does not resolve") from exc
    ips = {i[4][0] for i in infos}
    if not ips or not all(_is_public_ip(ip) for ip in ips):
        raise UnsafeURL("host resolves to a non-public address")
    return url
