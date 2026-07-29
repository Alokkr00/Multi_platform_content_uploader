"""
routes/sources.py — Feed Sources Management Endpoints
"""

import asyncio
import logging
import socket
import ipaddress
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException, Request

import db

logger = logging.getLogger("x_automation.routes.sources")

router = APIRouter(tags=["Sources"])


def validate_safe_url(url_str: str) -> str:
    """
    Validate that a URL uses http/https and does not target loopback,
    private, or cloud metadata IP addresses (SSRF Protection).
    """
    if not url_str:
        raise HTTPException(status_code=400, detail="URL cannot be empty")

    parsed = urlparse(url_str.strip())
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Only http and https schemes are permitted")

    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(status_code=400, detail="Invalid host in URL")

    # Block localhost / loopback string names directly
    if hostname.lower() in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        raise HTTPException(status_code=400, detail="Access to local IP address or loopback is blocked")

    try:
        ip_addr = socket.gethostbyname(hostname)
        ip = ipaddress.ip_address(ip_addr)
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            raise HTTPException(status_code=400, detail="Access to private or restricted network addresses is blocked")
    except socket.gaierror:
        raise HTTPException(status_code=400, detail=f"Could not resolve hostname: '{hostname}'")
    except ValueError:
        pass

    return url_str.strip()


@router.get("/api/sources")
async def get_sources():
    sources = await asyncio.to_thread(db.get_sources)
    return sources


@router.post("/api/sources")
async def add_source(request: Request):
    data = await request.json()
    url = data.get("url", "")
    name = data.get("name", "")
    platform = data.get("platform", "other")
    target_platforms = data.get("target_platforms", "x")

    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    # SSRF Protection
    validate_safe_url(url)

    source_id = await asyncio.to_thread(db.add_source, url, name, platform, target_platforms)
    if source_id is None:
        raise HTTPException(status_code=409, detail="Source URL already exists")

    logger.info(f"Source added: {name} ({url}) -> targets: {target_platforms}")
    return {"status": "ok", "id": source_id}


@router.delete("/api/sources/{source_id}")
async def remove_source(source_id: int):
    await asyncio.to_thread(db.delete_source, source_id)
    logger.info(f"Source removed: ID {source_id}")
    return {"status": "ok"}


@router.post("/api/sources/{source_id}/toggle")
async def toggle_source(source_id: int, request: Request):
    data = await request.json()
    active = data.get("active", True)
    await asyncio.to_thread(db.toggle_source, source_id, active)
    return {"status": "ok"}
