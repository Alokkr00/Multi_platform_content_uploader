"""
routes/accounts.py — Accounts Management Endpoints
"""

import os
import asyncio
import logging
from fastapi import APIRouter, HTTPException, Request

import db

logger = logging.getLogger("clipflow.routes.accounts")

router = APIRouter(tags=["Accounts"])


async def validate_account_credentials(platform: str, auth_mode: str, creds: dict, proxy_url: str = None, user_agent: str = None):
    """Test account credentials before saving to the database. Supports mock posting mode."""
    mock_posting = (await asyncio.to_thread(db.get_setting, "mock_posting", "false")).lower() == "true" or os.getenv("MOCK_POSTING", "false").lower() == "true"
    if mock_posting:
        logger.info(f"Pre-flight verification skipped (MOCK_POSTING is active) for platform {platform}")
        return True

    logger.info(f"Running pre-flight credentials verification for platform {platform} ({auth_mode})")
    try:
        if platform == "x":
            from publisher import XPublisher
            pub = XPublisher(
                label="verification_test",
                auth_mode=auth_mode,
                api_key=creds.get("api_key"),
                api_secret=creds.get("api_secret"),
                access_token=creds.get("access_token"),
                access_token_secret=creds.get("access_token_secret"),
                cookie_auth_token=creds.get("cookie_auth_token"),
                cookie_ct0=creds.get("cookie_ct0"),
                proxy_url=proxy_url,
                user_agent=user_agent
            )
            if auth_mode == "api":
                await asyncio.to_thread(pub.client.get_me)
            elif auth_mode == "cookie":
                await pub.twikit_client.get_user_by_screen_name("Twitter")
        elif platform == "instagram":
            from instagram_publisher import InstagramPublisher
            pub = InstagramPublisher(
                label="verification_test",
                auth_mode=auth_mode,
                credentials={
                    **creds,
                    "proxy_url": proxy_url,
                    "user_agent": user_agent
                }
            )
            if auth_mode == "cookie":
                await asyncio.to_thread(pub._login_instagrapi)
        elif platform == "youtube":
            from youtube_publisher import YouTubePublisher
            pub = YouTubePublisher(
                label="verification_test",
                client_id=creds.get("client_id"),
                client_secret=creds.get("client_secret"),
                refresh_token=creds.get("refresh_token")
            )
            await pub._ensure_access_token()
        return True
    except Exception as e:
        logger.warning(f"Pre-flight verification failed: {e}")
        raise ValueError(f"Credentials validation failed")


@router.get("/api/accounts")
async def get_accounts():
    accounts = await asyncio.to_thread(db.get_accounts, decrypt=False)
    for acct in accounts:
        acct.pop("credentials_enc", None)
    return accounts


@router.post("/api/accounts")
async def add_account(request: Request):
    data = await request.json()
    label = data.get("label")
    label = label.strip() if label else ""
    
    platform = data.get("platform")
    platform = platform.strip().lower() if platform else "x"
    
    auth_mode = data.get("auth_mode")
    auth_mode = auth_mode.strip().lower() if auth_mode else "api"
    
    proxy_url = data.get("proxy_url")
    proxy_url = proxy_url.strip() if proxy_url else None
    
    user_agent = data.get("user_agent")
    user_agent = user_agent.strip() if user_agent else None
    
    creds = data.get("credentials")
    if not creds or not isinstance(creds, dict):
        creds = {}
        for k, v in data.items():
            if k not in ("label", "platform", "auth_mode", "proxy_url", "user_agent"):
                creds[k] = v

    if not label:
        raise HTTPException(status_code=400, detail="Label is required")

    try:
        try:
            await validate_account_credentials(platform, auth_mode, creds, proxy_url, user_agent)
        except Exception:
            raise HTTPException(status_code=400, detail="Account credentials verification failed")

        account_id = await asyncio.to_thread(
            db.add_account, 
            label=label, 
            platform=platform,
            auth_mode=auth_mode, 
            credentials=creds,
            proxy_url=proxy_url,
            user_agent=user_agent
        )
        logger.info(f"Account added: {label} (platform: {platform}, mode: {auth_mode})")
        return {"status": "ok", "id": account_id}
    except HTTPException:
        raise
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(status_code=409, detail=f"Account '{label}' already exists on {platform}")
        raise HTTPException(status_code=500, detail="Internal server error adding account")


@router.delete("/api/accounts/{label}")
async def remove_account(label: str, platform: str = None):
    await asyncio.to_thread(db.delete_account, label, platform)
    logger.info(f"Account removed: {label} ({platform or 'all'})")
    return {"status": "ok"}


@router.get("/api/accounts/health")
async def get_accounts_health():
    health_summary = await asyncio.to_thread(db.get_account_health_summary)
    return health_summary
