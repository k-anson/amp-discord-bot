"""
Minimal async client for the CubeCoders AMP API (Application Deployment System / ADS controller).

Docs / API browser: open https://YOUR-AMP-URL/API in a browser while logged in - AMP ships
a self-documenting API explorer that lists every module/method and its exact response shape
for YOUR version. Field names below (e.g. metric keys) are based on common AMP versions but
CAN drift between releases, so this client is written defensively:
  - it searches metric dictionaries by substring ("cpu", "memory", "user") instead of exact key
  - set debug_dump_raw=True in config to print raw JSON so you can adjust parsing if needed
"""

import time
import logging
import aiohttp

log = logging.getLogger("amp_client")


class AMPError(Exception):
    pass


class AMPClient:
    def __init__(self, base_url: str, username: str, password: str, verify_ssl: bool = True):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.session_id: str | None = None
        self._http: aiohttp.ClientSession | None = None

    async def _get_http(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            connector = aiohttp.TCPConnector(ssl=self.verify_ssl)
            self._http = aiohttp.ClientSession(connector=connector)
        return self._http

    async def close(self):
        if self._http and not self._http.closed:
            await self._http.close()

    async def _call(self, endpoint: str, payload: dict | None = None, retry_on_auth: bool = True):
        """POST to {base_url}/API/{endpoint} with SESSIONID injected automatically."""
        http = await self._get_http()
        body = dict(payload or {})
        if self.session_id:
            body["SESSIONID"] = self.session_id

        url = f"{self.base_url}/API/{endpoint}"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}

        async with http.post(url, json=body, headers=headers) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise AMPError(f"{endpoint} HTTP {resp.status}: {text[:300]}")
            data = await resp.json()

        # AMP returns a "Status": false / error style payload, or missing session, on auth issues
        if isinstance(data, dict) and data.get("Title") == "Unauthorized" and retry_on_auth:
            await self.login(force=True)
            return await self._call(endpoint, payload, retry_on_auth=False)

        return data

    async def login(self, force: bool = False):
        if self.session_id and not force:
            return
        payload = {
            "username": self.username,
            "password": self.password,
            "token": "",
            "rememberMe": False,
        }
        data = await self._call("Core/Login", payload, retry_on_auth=False)
        if not data or not data.get("success", data.get("Success", False)):
            raise AMPError(f"AMP login failed: {data}")
        self.session_id = data.get("sessionID") or data.get("SessionID")
        if not self.session_id:
            raise AMPError(f"AMP login response had no session id: {data}")

    async def get_instances(self, debug_dump_raw: bool = False) -> list[dict]:
        """Returns a flat list of instance dicts from the ADS controller."""
        await self.login()
        data = await self._call("ADSModule/GetInstances", {})

        if debug_dump_raw:
            import json
            log.info("=== RAW GetInstances ===\n%s", json.dumps(data, indent=2)[:4000])

        instances: list[dict] = []

        def collect(node):
            if isinstance(node, dict):
                if "AvailableInstances" in node:
                    instances.extend(node["AvailableInstances"])
                elif "InstanceName" in node or "InstanceID" in node:
                    instances.append(node)
            elif isinstance(node, list):
                for item in node:
                    collect(item)

        collect(data)
        # The ADS controller lists itself as an instance too (Module == "ADS") -
        # exclude it since it's not a game server and has no player/CPU/memory metrics.
        return [inst for inst in instances if inst.get("Module") != "ADS"]


def is_running(instance: dict) -> bool:
    if "Running" in instance:
        return bool(instance["Running"])
    state = str(instance.get("State", "")).lower()
    return state in ("running", "40", "ready")


def find_metric(instance: dict, *keywords: str):
    """Find a metric dict whose key matches any of the given lowercase keywords."""
    metrics = instance.get("Metrics") or {}
    for key, value in metrics.items():
        low = key.lower()
        if any(kw in low for kw in keywords):
            return value
    return None


def format_metric_percent(metric: dict | None) -> str | None:
    if not metric:
        return None
    percent = metric.get("Percent")
    if percent is not None:
        return f"{round(percent)}%"
    raw, cap = metric.get("RawValue"), metric.get("MaxValue")
    if raw is not None and cap:
        return f"{round((raw / cap) * 100)}%"
    return None


def format_metric_fraction(metric: dict | None) -> str | None:
    if not metric:
        return None
    raw, cap = metric.get("RawValue"), metric.get("MaxValue")
    if raw is not None and cap is not None:
        return f"{int(raw)}/{int(cap)}"
    if raw is not None:
        return str(int(raw))
    return None