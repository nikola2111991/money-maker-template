"""Morning alert: due follow-ups + stats summary to Discord."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import (
    FOLLOWUP_SCHEDULE,
    find_leads_dir,
    get_lead_status,
    load_status,
    scan_all_leads,
)

WEBHOOK_URL = os.environ.get("MM_DISCORD_WEBHOOK", "") or os.environ.get("SCANNER_DISCORD_WEBHOOK", "")


def main() -> None:
    if not WEBHOOK_URL:
        print("MM_DISCORD_WEBHOOK or SCANNER_DISCORD_WEBHOOK required")
        return

    leads_dir = find_leads_dir()
    status = load_status(leads_dir)
    all_leads = scan_all_leads(leads_dir)

    # Counts
    ready = sum(1 for l in all_leads if get_lead_status(status, l["key"]) == "ready")
    contacted = sum(1 for e in status["leads"].values() if e.get("contacted_date"))
    converted = sum(1 for e in status["leads"].values() if e.get("deal_value") is not None)
    revenue = sum(e.get("deal_value", 0) for e in status["leads"].values() if e.get("deal_value"))

    # Due follow-ups
    today_dt = datetime.now(timezone.utc).date()
    due_count = 0
    for entry in status["leads"].values():
        if entry.get("outcome"):
            continue
        contacted_date = entry.get("contacted_date")
        if not contacted_date:
            continue
        c_dt = datetime.strptime(contacted_date, "%Y-%m-%d").date()
        sent_types = {f["type"] for f in entry.get("followups", [])}
        for fu_type, fu_days in FOLLOWUP_SCHEDULE.items():
            if fu_type not in sent_types:
                if (c_dt + timedelta(days=fu_days) - today_dt).days <= 0:
                    due_count += 1
                break

    today_str = today_dt.strftime("%d.%m.%Y")
    embed = {
        "title": f"Money Maker Daily - {today_str}",
        "color": 0xC8956C,
        "fields": [
            {"name": "Follow-ups due", "value": str(due_count), "inline": True},
            {"name": "Ready to send", "value": str(ready), "inline": True},
            {"name": "Contacted", "value": str(contacted), "inline": True},
            {"name": "Converted", "value": str(converted), "inline": True},
            {"name": "Revenue", "value": f"{revenue} EUR", "inline": True},
        ],
    }

    payload = json.dumps({"embeds": [embed]}).encode()
    req = Request(WEBHOOK_URL, data=payload, headers={
        "Content-Type": "application/json",
        "User-Agent": "MoneyMaker/1.0",
    })
    urlopen(req)
    print("Discord alert sent.")


if __name__ == "__main__":
    main()
