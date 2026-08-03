from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

import httpx

BASE_URL = "https://postal-ninja.p.rapidapi.com/v1"

# Codes with this prefix are simulated locally from FAKE_FILE instead of hitting
# the real Postal Ninja API — used to test the notification flow without burning
# the API's monthly/daily quota or waiting on a real courier.
FAKE_PREFIX = "TEST"
FAKE_FILE = Path(__file__).parent / "test_packages.json"


def _format_dt(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    return dt.strftime("%d/%m %H:%M")


def _merge_events(events: list[dict]) -> list[dict]:
    # Cainiao is the international overlay tracker; it reports the same real-world
    # event as the local last-mile courier (in English) at the same timestamp.
    # When both exist for a dt, keep the local courier's version.
    merged: dict[str, dict] = {}
    order: list[str] = []
    for event in events:
        dt = event.get("dt")
        if dt not in merged:
            merged[dt] = event
            order.append(dt)
            continue
        if merged[dt].get("carrier") == "Cainiao" and event.get("carrier") != "Cainiao":
            merged[dt] = event
    return [merged[dt] for dt in order]


def _format_pkg(pkg: dict, full: bool = True) -> str:
    events = pkg.get("events", [])
    if not events:
        return "📦 Registrado, todavía sin movimientos. Reintentá en un rato."

    delivered = pkg.get("state", {}).get("Delivered")
    status_line = "✅ Estado: ENTREGADO" if delivered else "🚚 Estado: en camino"

    merged = _merge_events(events)
    if not full:
        last = merged[-1]
        return f"{status_line} — {_format_dt(last.get('dt', ''))}: {last.get('dsc', '')}"

    lines = [status_line, ""]
    for event in merged:
        carrier = event.get("carrier") or "?"
        marker = "≈ (estimado)" if not event.get("id") else "•"
        lines.append(f"{marker} {_format_dt(event.get('dt', ''))} [{carrier}] — {event.get('dsc', '')}")

    return "\n".join(lines)


def _load_fake(code: str) -> dict:
    if not FAKE_FILE.exists():
        return {"events": [], "state": {"Delivered": False}}
    data = json.loads(FAKE_FILE.read_text())
    return data.get(code, {"events": [], "state": {"Delivered": False}})


class TrackingClient:
    def __init__(self, api_key: str):
        self._headers = {
            "x-rapidapi-host": "postal-ninja.p.rapidapi.com",
            "x-rapidapi-key": api_key,
        }

    async def create_and_fetch(self, number: str) -> tuple[int, str]:
        """Registers a new tracker with Postal Ninja and returns (pkg_id, formatted text).

        Uses up the plan's monthly "new shipments" quota — only call this once per
        tracking code, then reuse the pkg_id with `fetch`.
        """
        if number.startswith(FAKE_PREFIX):
            return 0, _format_pkg(_load_fake(number))

        async with httpx.AsyncClient(timeout=30, headers=self._headers) as client:
            create_resp = await client.post(f"{BASE_URL}/track", data={"trackCode": number})
            create_resp.raise_for_status()
            pkg_id = create_resp.json()["pkgId"]

            await asyncio.sleep(3)
            data = await self._get(client, pkg_id)

        return pkg_id, _format_pkg(data.get("pkg", {}))

    async def fetch(self, pkg_id: int, code: str, full: bool = True) -> str:
        """Re-fetches an already-created tracker. Only counts against the daily request quota."""
        if code.startswith(FAKE_PREFIX):
            return _format_pkg(_load_fake(code), full=full)

        async with httpx.AsyncClient(timeout=30, headers=self._headers) as client:
            data = await self._get(client, pkg_id)
        return _format_pkg(data.get("pkg", {}), full=full)

    async def _get(self, client: httpx.AsyncClient, pkg_id: int) -> dict:
        resp = await client.get(f"{BASE_URL}/track/{pkg_id}")
        resp.raise_for_status()
        return resp.json()
