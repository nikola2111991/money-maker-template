"""maintenance.py - Monthly maintenance check for active client sites.

Checks:
1. Site uptime (HTTP 200)
2. Google rating changes (Places API)
3. New reviews count
4. SSL certificate status

Generates a report per client for forwarding.

Usage:
    python3 maintenance.py                    # Check all clients
    python3 maintenance.py --client slug      # Check one client
    python3 maintenance.py --json             # Output as JSON
"""
from __future__ import annotations

import argparse
import json
import ssl
import socket
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import DEPLOY_BASE_URL, DEPLOY_REPO, GOOGLE_API_KEY


MAINTENANCE_FILE = "maintenance_status.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_maintenance(path: Path) -> dict[str, Any]:
    """Load previous maintenance data."""
    fp = path / MAINTENANCE_FILE
    if fp.exists():
        return json.loads(fp.read_text())
    return {"clients": {}, "last_run": ""}


def _save_maintenance(path: Path, data: dict[str, Any]) -> None:
    """Save maintenance data atomically."""
    fp = path / MAINTENANCE_FILE
    tmp = fp.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(fp)


def check_uptime(url: str, timeout: int = 10) -> dict[str, Any]:
    """Check if site returns HTTP 200."""
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"status": "up", "code": resp.status}
    except urllib.error.HTTPError as e:
        return {"status": "down", "code": e.code}
    except Exception as e:
        return {"status": "error", "code": 0, "error": str(e)}


def check_ssl(hostname: str) -> dict[str, Any]:
    """Check SSL certificate expiry."""
    try:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(socket.socket(), server_hostname=hostname) as s:
            s.settimeout(5)
            s.connect((hostname, 443))
            cert = s.getpeercert()
            expiry = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
            days_left = (expiry - datetime.now()).days
            return {"valid": True, "expires": cert["notAfter"], "days_left": days_left}
    except Exception as e:
        return {"valid": False, "error": str(e)}


def check_google_rating(place_name: str, city: str, api_key: str) -> dict[str, Any]:
    """Fetch current Google rating via Places API text search."""
    if not api_key:
        return {"error": "No GOOGLE_API_KEY configured"}

    query = urllib.parse.quote(f"{place_name} {city}")
    url = f"https://maps.googleapis.com/maps/api/place/textsearch/json?query={query}&key={api_key}"

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
            if data.get("results"):
                place = data["results"][0]
                return {
                    "rating": place.get("rating", 0),
                    "review_count": place.get("user_ratings_total", 0),
                }
            return {"error": "Place not found"}
    except Exception as e:
        return {"error": str(e)}


def run_check(
    slug: str,
    schema_path: Path,
    previous: dict[str, Any],
) -> dict[str, Any]:
    """Run all checks for one client site."""
    # Read schema for business info
    if not schema_path.exists():
        return {"error": f"Schema not found: {schema_path}"}

    schema = json.loads(schema_path.read_text())
    name = schema.get("name", slug)
    city = schema.get("city", "")
    site_url = f"{DEPLOY_BASE_URL}/{slug}/"

    report: dict[str, Any] = {
        "name": name,
        "slug": slug,
        "checked_at": _now(),
        "site_url": site_url,
    }

    # 1. Uptime check
    report["uptime"] = check_uptime(site_url)

    # 2. SSL check (extract hostname from URL)
    try:
        hostname = site_url.split("//")[1].split("/")[0]
        report["ssl"] = check_ssl(hostname)
    except (IndexError, Exception):
        report["ssl"] = {"valid": False, "error": "Could not parse hostname"}

    # 3. Google rating
    google = check_google_rating(name, city, GOOGLE_API_KEY)
    report["google"] = google

    # Compare with previous
    prev_rating = previous.get("google", {}).get("rating", 0)
    prev_reviews = previous.get("google", {}).get("review_count", 0)
    current_rating = google.get("rating", 0)
    current_reviews = google.get("review_count", 0)

    report["changes"] = {
        "rating_change": round(current_rating - prev_rating, 1) if prev_rating else 0,
        "new_reviews": current_reviews - prev_reviews if prev_reviews else 0,
    }

    return report


def format_report(report: dict[str, Any]) -> str:
    """Format a client report as readable text for forwarding."""
    lines = [
        f"Monthly Report: {report['name']}",
        f"Date: {report['checked_at'][:10]}",
        "",
    ]

    # Uptime
    uptime = report.get("uptime", {})
    status = "OK" if uptime.get("status") == "up" else "DOWN"
    lines.append(f"Site status: {status}")
    lines.append(f"URL: {report['site_url']}")

    # SSL
    ssl_info = report.get("ssl", {})
    if ssl_info.get("valid"):
        lines.append(f"SSL: Valid ({ssl_info.get('days_left', '?')} days remaining)")
    else:
        lines.append(f"SSL: {ssl_info.get('error', 'Unknown')}")

    # Google
    google = report.get("google", {})
    if google.get("error"):
        lines.append(f"Google: {google['error']}")
    else:
        rating = google.get("rating", 0)
        reviews = google.get("review_count", 0)
        lines.append(f"Google rating: {rating}/5 ({reviews} reviews)")

    # Changes
    changes = report.get("changes", {})
    rating_change = changes.get("rating_change", 0)
    new_reviews = changes.get("new_reviews", 0)
    if rating_change:
        direction = "up" if rating_change > 0 else "down"
        lines.append(f"Rating: {direction} {abs(rating_change)} since last check")
    if new_reviews:
        lines.append(f"New reviews: +{new_reviews} since last check")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Monthly maintenance check for client sites")
    parser.add_argument("--client", help="Check only this slug")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--data-dir", help="Override data directory (default: DEPLOY_REPO)")
    args = parser.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else DEPLOY_REPO

    # Load previous maintenance data
    maintenance = _load_maintenance(data_dir)

    # Find client sites (each subdirectory with a schema.json)
    clients: list[tuple[str, Path]] = []
    if args.client:
        schema_path = data_dir / args.client / "schema.json"
        if schema_path.exists():
            clients.append((args.client, schema_path))
        else:
            print(f"ERROR: Client '{args.client}' not found in {data_dir}")
            return 1
    else:
        for d in sorted(data_dir.iterdir()):
            if d.is_dir() and (d / "schema.json").exists():
                clients.append((d.name, d / "schema.json"))

    if not clients:
        print(f"No client sites found in {data_dir}")
        return 0

    reports: list[dict[str, Any]] = []
    for slug, schema_path in clients:
        previous = maintenance["clients"].get(slug, {})
        report = run_check(slug, schema_path, previous)
        reports.append(report)

        # Update stored data for next comparison
        maintenance["clients"][slug] = report

    maintenance["last_run"] = _now()
    _save_maintenance(data_dir, maintenance)

    # Output
    if args.json:
        print(json.dumps(reports, indent=2, ensure_ascii=False))
    else:
        for report in reports:
            print("=" * 50)
            print(format_report(report))
            print()
        print(f"Checked {len(reports)} client(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
