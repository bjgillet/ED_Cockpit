"""
ED Cockpit — Spansh Fleet Carrier Route API Client
====================================================
Blocking helper that submits a fleet-carrier route planning job to the
Spansh API, polls for completion, and returns the ordered waypoint list.

Designed to be called inside a thread or asyncio executor so it never
blocks the main or asyncio threads.

Uses only the Python standard library (urllib + json) — no new dependencies.

API overview
------------
  POST  https://spansh.co.uk/api/fleetcarrier/route
        Form body: source=<name>&destination=<name>&range=<LY>
        Response:  {"job": "<uuid>", "status": "queued"}

  GET   https://spansh.co.uk/api/results/<job_id>
        Response when pending: {"status": "queued"|"working", ...}
        Response when done:    {"status": "ok", "result": {...}}
        Response on error:     {"status": "error", "error": "..."}

Result waypoint structure
--------------------------
Each element returned by ``fetch_fleet_carrier_route`` is a dict:

    {
        "system":    str,    # system name
        "x":         float,  # galactic X coordinate (LY)
        "y":         float,  # galactic Y coordinate (LY)
        "z":         float,  # galactic Z coordinate (LY)
        "distance":  float,  # distance jumped from previous waypoint (LY)
        "fuel_cost": float,  # tritium cost for this hop (tonnes)
    }

The first waypoint is always the source system with distance=0 and
fuel_cost=0.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_BASE_URL        = "https://spansh.co.uk/api"
_ROUTE_ENDPOINT  = f"{_BASE_URL}/fleetcarrier/route"
_RESULT_ENDPOINT = f"{_BASE_URL}/results"

_POLL_INTERVAL:  float = 2.0    # seconds between result polls
_POLL_TIMEOUT:   float = 120.0  # maximum wait time before giving up
_HTTP_TIMEOUT:   float = 15.0   # per-request HTTP timeout (seconds)

_HEADERS = {
    "User-Agent": "ED-Cockpit/1.0 (Fleet Carrier Route Planner)",
    "Accept":     "application/json",
}


class SpanshRouteError(Exception):
    """Raised when the Spansh API returns an error or times out."""


# ── Public API ─────────────────────────────────────────────────────────────────

def fetch_fleet_carrier_route(
    source:      str,
    destination: str,
    jump_range:  float = 500.0,
) -> list[dict[str, Any]]:
    """
    Plan a fleet-carrier route and return the ordered waypoint list.

    Blocking — run inside a thread or asyncio executor.

    Parameters
    ----------
    source : str
        Name of the source star system (current FC position).
    destination : str
        Name of the destination star system.
    jump_range : float
        Fleet carrier jump range in light years (default 500 LY).

    Returns
    -------
    list[dict]
        Ordered list of waypoints.  See module docstring for the dict
        schema.  Always contains at least two entries (source + destination).

    Raises
    ------
    SpanshRouteError
        If the API returns an error, the job times out, or the response
        cannot be parsed.
    """
    log.info(
        "Spansh: planning FC route  %r → %r  (range=%.0f LY)",
        source, destination, jump_range,
    )

    job_id = _submit_job(source, destination, jump_range)
    log.debug("Spansh: job submitted — id=%s", job_id)

    result = _poll_result(job_id)
    waypoints = _parse_waypoints(result)

    log.info(
        "Spansh: route ready — %d waypoints, total distance=%.0f LY, "
        "total tritium=%.0f t",
        len(waypoints),
        sum(w["distance"]  for w in waypoints),
        sum(w["fuel_cost"] for w in waypoints),
    )
    return waypoints


# ── Internal helpers ───────────────────────────────────────────────────────────

def _submit_job(source: str, destination: str, jump_range: float) -> str:
    """Submit the route planning job and return the job ID."""
    body = urllib.parse.urlencode({
        "source":      source,
        "destination": destination,
        "range":       str(int(jump_range)),
    }).encode("utf-8")

    req = urllib.request.Request(
        _ROUTE_ENDPOINT,
        data=body,
        method="POST",
        headers={
            **_HEADERS,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SpanshRouteError(
            f"HTTP {exc.code} submitting route job: {exc.reason}"
        ) from exc
    except Exception as exc:
        raise SpanshRouteError(f"Error submitting route job: {exc}") from exc

    job_id = data.get("job")
    if not job_id:
        raise SpanshRouteError(
            f"No job ID in Spansh response: {data}"
        )
    return str(job_id)


def _poll_result(job_id: str) -> dict:
    """Poll the results endpoint until the job is complete or times out."""
    url = f"{_RESULT_ENDPOINT}/{urllib.parse.quote(job_id)}"
    req = urllib.request.Request(url, headers=_HEADERS)

    deadline = time.monotonic() + _POLL_TIMEOUT
    attempt  = 0

    while True:
        attempt += 1
        try:
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise SpanshRouteError(
                f"Error polling results for job {job_id}: {exc}"
            ) from exc

        status = data.get("status", "")

        if status == "ok":
            return data.get("result", {})

        if status == "error":
            raise SpanshRouteError(
                f"Spansh route job failed: {data.get('error', 'unknown error')}"
            )

        if time.monotonic() >= deadline:
            raise SpanshRouteError(
                f"Spansh route job {job_id!r} timed out after "
                f"{_POLL_TIMEOUT:.0f} s (last status: {status!r})"
            )

        log.debug(
            "Spansh: job %s still %r (attempt %d) — retrying in %.0f s",
            job_id, status, attempt, _POLL_INTERVAL,
        )
        time.sleep(_POLL_INTERVAL)


def _parse_waypoints(result: dict) -> list[dict[str, Any]]:
    """Convert the raw Spansh result dict into our normalised waypoint list."""
    jumps = result.get("jumps", [])
    if not jumps:
        raise SpanshRouteError("Spansh returned an empty waypoint list.")

    waypoints: list[dict[str, Any]] = []
    for hop in jumps:
        waypoints.append({
            "system":    str(hop.get("system", "")),
            "x":         float(hop.get("x", 0.0)),
            "y":         float(hop.get("y", 0.0)),
            "z":         float(hop.get("z", 0.0)),
            "distance":  float(hop.get("distance_jumped", 0.0)),
            "fuel_cost": float(hop.get("fuel_cost",       0.0)),
        })

    return waypoints
