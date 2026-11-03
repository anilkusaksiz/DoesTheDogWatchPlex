"""
DoesTheDogDie.com API client with local JSON file caching.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

DTDD_BASE = "https://www.doesthedogdie.com"
CACHE_DIR = Path(__file__).parent / ".cache"


class DTDDClient:
    def __init__(self, api_key: str, cache_ttl: int = 604800, api_delay: float = 2.0):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "X-API-KEY": api_key,
        })
        self.cache_ttl = cache_ttl
        self.api_delay = api_delay
        self._last_request_time = 0.0
        self._remaining_minute: int | None = None
        # Monthly quota left, from DTDD's response headers (free tier: 5000/month)
        self.remaining_month: int | None = None
        self.requests_made = 0
        CACHE_DIR.mkdir(exist_ok=True)

    def _rate_limit(self):
        """Enforce delay between API calls, and wait out the minute window when it is used up."""
        if self._remaining_minute is not None and self._remaining_minute <= 1:
            print("    (DTDD minute quota used up, waiting 60s)")
            time.sleep(60)
            self._remaining_minute = None
        elapsed = time.time() - self._last_request_time
        if elapsed < self.api_delay:
            time.sleep(self.api_delay - elapsed)
        self._last_request_time = time.time()

    def _get(self, path: str, params: dict | None = None) -> dict:
        """GET a DTDD endpoint (30/min, 5000/month on the free tier).

        A 429 waits a full minute before one retry: DTDD's Retry-After of 1-2s
        is shorter than its minute window, and every retry costs a request.
        """
        for attempt in range(2):
            self._rate_limit()
            resp = self.session.get(f"{DTDD_BASE}{path}", params=params, allow_redirects=False)
            self.requests_made += 1
            headers = resp.headers
            if "x-ratelimit-remaining-minute" in headers:
                self._remaining_minute = int(headers["x-ratelimit-remaining-minute"])
            if "x-ratelimit-remaining-month" in headers:
                self.remaining_month = int(headers["x-ratelimit-remaining-month"])
            if resp.status_code != 429:
                if resp.is_redirect:
                    raise requests.HTTPError(f"Unexpected redirect to {headers.get('location')} "
                                             f"(each redirect costs an extra request)")
                resp.raise_for_status()
                return resp.json()
            if attempt == 0:
                print("    (rate limited by DTDD, waiting 60s)")
                time.sleep(60)
        resp.raise_for_status()

    def _get_cache(self, key: str) -> dict | None:
        """Read from local JSON cache if fresh."""
        path = CACHE_DIR / f"{key}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            if time.time() - data.get("_cached_at", 0) < self.cache_ttl:
                return data.get("_payload")
        except (json.JSONDecodeError, KeyError):
            pass
        return None

    def _set_cache(self, key: str, payload: dict):
        """Write to local JSON cache."""
        path = CACHE_DIR / f"{key}.json"
        path.write_text(json.dumps({
            "_cached_at": time.time(),
            "_payload": payload,
        }, indent=2))

    def search(self, query: str) -> list[dict]:
        """Search DTDD by title string. Returns list of matching items."""
        cache_key = f"search_{query.lower().replace(' ', '_')}"
        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached

        items = self._get("/dddsearch", {"q": query}).get("items", [])
        self._set_cache(cache_key, items)
        return items

    def search_by_imdb(self, imdb_id: str) -> list[dict]:
        """Search DTDD by IMDB ID (e.g., 'tt1234567'). Returns list of matching items."""
        cache_key = f"imdb_{imdb_id}"
        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached

        items = self._get("/dddsearch", {"imdb": imdb_id}).get("items", [])
        self._set_cache(cache_key, items)
        return items

    def get_media(self, item_id: int) -> dict:
        """Get full trigger/warning data for a DTDD media item."""
        cache_key = f"media_{item_id}"
        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached

        # The bare /media/{id} URL 301-redirects here, which would cost a second request
        data = self._get(f"/media/{item_id}", {"index1": -1, "index2": -1})
        self._set_cache(cache_key, data)
        return data

    def clear_cache(self):
        """Remove all cached files."""
        if CACHE_DIR.exists():
            for f in CACHE_DIR.glob("*.json"):
                f.unlink()
            print(f"Cache cleared ({CACHE_DIR})")
