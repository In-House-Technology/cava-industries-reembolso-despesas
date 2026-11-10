"""The gateway's actor JWT is distinct from browser forward-auth headers."""
import os
import re
import time
from threading import Lock

import httpx
import jwt
from fastapi import HTTPException, Request

_keys = {"url": None, "keys": [], "at": 0.0}
_lock = Lock()


def gateway_keys(url, kid):
    with _lock:
        age = time.monotonic() - _keys["at"]
        unknown = not any(key.get("kid") == kid for key in _keys["keys"])
        if _keys["url"] != url or age > 300 or (unknown and age > 30):
            response = httpx.get(url, timeout=5, follow_redirects=False)
            response.raise_for_status()
            _keys.update(url=url, keys=response.json().get("keys", []), at=time.monotonic())
        return _keys["keys"]


def actor(request: Request):
    match = re.fullmatch(r"Bearer\s+(\S+)", request.headers.get("authorization", ""), re.I)
    issuer, url = os.getenv("IHC_MCP_ISSUER"), os.getenv("IHC_MCP_JWKS_URL")
    try:
        if not match or not issuer or not url:
            raise ValueError("Missing gateway configuration or bearer token")
        header = jwt.get_unverified_header(match[1])
        if header.get("alg") != "RS256" or not header.get("kid"):
            raise ValueError("Expected RS256 and kid")
        key = next(key for key in gateway_keys(url, header["kid"]) if key.get("kid") == header["kid"])
        claims = jwt.decode(
            match[1], jwt.PyJWK.from_dict(key, algorithm="RS256").key,
            algorithms=["RS256"], issuer=issuer,
            audience=os.getenv("IHC_MCP_AUDIENCE", f"{os.getenv('APP_SLUG', 'reembolso-despesas')}.mcp"),
            leeway=30, options={"require": ["exp", "iss", "aud"]},
        )
        email = claims.get("email") or claims.get("sub")
        if not isinstance(email, str) or not email:
            raise ValueError("Missing actor")
        return {"email": email, "groups": claims.get("groups", []),
                "scopes": str(claims.get("scope", "")).split(), "confirmed_by": claims.get("confirmed_by")}
    except (ValueError, KeyError, TypeError, StopIteration, jwt.PyJWTError, httpx.HTTPError):
        raise HTTPException(401, detail="A valid actor JWT from the company MCP gateway is required") from None


def require_read(actor):
    scope = f"{os.getenv('APP_SLUG', 'reembolso-despesas')}.read"
    if scope not in actor["scopes"]:
        raise HTTPException(403, detail=f"Tool needs scope {scope}")
