"""A neutral fallback keeps local development and upstream outages usable."""
import os
import time
from pathlib import Path
from threading import Lock

import httpx
from fastapi import Response

DEFAULT = (Path(__file__).parent / "theme.css").read_text()
_cache = {"url": None, "css": DEFAULT, "at": 0.0}
_lock = Lock()


def theme():
    url = os.getenv("IHC_THEME_URL", "")
    with _lock:
        if _cache["url"] != url or time.monotonic() - _cache["at"] >= 60:
            css = DEFAULT
            if url:
                try:
                    response = httpx.get(url, timeout=2, follow_redirects=False)
                    response.raise_for_status()
                    if not response.text.strip():
                        raise ValueError("Empty theme")
                    css = response.text
                except (httpx.HTTPError, ValueError):
                    pass
            _cache.update(url=url, css=css, at=time.monotonic())
        return Response(_cache["css"], media_type="text/css")
