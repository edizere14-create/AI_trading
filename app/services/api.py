from __future__ import annotations

import requests

def api_request(method, url, params=None, json=None):
    try:
        resp = requests.request(method, url, params=params, json=json, timeout=2)
        resp.raise_for_status()
        return resp.json(), None
    except Exception as e:
        return None, str(e)

def check_connection(url: str) -> bool:
    for path in ("/health", "/api/health"):
        _, err = api_request("GET", f"{url}{path}")
        if not err:
            return True
    return False