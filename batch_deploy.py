"""
batch_deploy.py - Batch render and deploy all leads to GitHub Pages

Usage:
  python3 batch_deploy.py                                    # deploy all from config.LEADS_DIR
  python3 batch_deploy.py /path/to/leads                     # deploy all from custom path
  python3 batch_deploy.py --dry-run                          # render only, no deploy
  python3 batch_deploy.py --only HOT                         # only HOT category
  python3 batch_deploy.py --only HOT,WARM                    # HOT + WARM
  python3 batch_deploy.py --playbook playbooks/tiler-au.json # with playbook
  python3 batch_deploy.py --enrich --playbook playbooks/tiler-au.json  # Claude CLI enrichment ($0 via Max)
  python3 batch_deploy.py --with-copy --playbook playbooks/tiler-au.json  # API copy generation
"""
import json
import os
import sys
import csv
import shutil
import argparse
import logging
import urllib.parse
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from render import validate, render_templates, deploy_to_github, enrich_schema
from copy_generator import generate_site_copy, generate_outreach
from enrich import (
    build_enrichment_prompt,
    call_claude,
    merge_enriched,
)
from pipeline import load_status, save_status


def _build_outreach_html(
    data: dict, outreach: dict, site_url: str, lead_folder: str, playbook: dict
) -> None:
    """Populate outreach-template.html with generated copy and save to lead folder."""
    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outreach-template.html")
    if not os.path.exists(template_path):
        return

    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()

    phone_prefix = playbook.get("phone_prefix", "+1").replace("+", "")
    lang = playbook.get("i18n", {}).get("lang", "en")

    replacements = {
        "{{ LANG|default('en') }}": lang,
        "{{ NAZIV }}": data.get("name", ""),
        "{{ PHONE_PREFIX }}": phone_prefix,
        "{{ MOBILNI }}": data.get("phone", ""),
        "{{ DEMO_URL }}": site_url,
        "{{ EMAIL }}": data.get("email", ""),
        "{{ EMAIL_SUBJECT }}": outreach.get("email_subject", ""),
        "{{ WHATSAPP_DAN0 }}": outreach.get("whatsapp_initial", ""),
        "{{ EMAIL_DAN0 }}": outreach.get("email_initial", ""),
        "{{ FOLLOWUP_1 }}": outreach.get("followup_1", ""),
        "{{ FOLLOWUP_2 }}": outreach.get("followup_2", ""),
        "{{ FOLLOWUP_3 }}": outreach.get("followup_3", ""),
        "{{ WHATSAPP_DAN0_ENCODED }}": urllib.parse.quote(outreach.get("whatsapp_initial", "")),
    }

    for placeholder, value in replacements.items():
        html = html.replace(placeholder, value)

    slug = data.get("slug", "lead")
    out_path = os.path.join(lead_folder, f"{slug}-outreach.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


def find_leads(leads_dir: str, categories: Optional[List[str]] = None) -> List[Dict[str, str]]:
    """Find all lead folders with schema_draft.json.

    Returns list of dicts: {category, folder, schema_path, name}
    """
    if categories is None:
        categories = list(config.CATEGORIES)

    leads: List[Dict[str, str]] = []

    for cat in categories:
        cat_dir = os.path.join(leads_dir, cat)
        if not os.path.isdir(cat_dir):
            continue

        for folder_name in sorted(os.listdir(cat_dir)):
            folder_path = os.path.join(cat_dir, folder_name)
            if not os.path.isdir(folder_path):
                continue

            schema_path = os.path.join(folder_path, "schema_draft.json")
            if not os.path.exists(schema_path):
                continue

            leads.append({
                "category": cat,
                "folder": folder_path,
                "folder_name": folder_name,
                "schema_path": schema_path,
            })

    return leads


def load_and_validate(schema_path: str, playbook: Optional[dict] = None) -> tuple:
    """Load JSON, return (data, errors) or (None, error_msg)."""
    try:
        with open(schema_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError, PermissionError) as e:
        return None, str(e)

    # Validate schema structure
    try:
        from models import SchemaDraft
        SchemaDraft(**data)
    except Exception as e:
        return None, f"Schema validation: {e}"

    # Remove meta fields
    data = {k: v for k, v in data.items() if not k.startswith("_")}

    # Enrich before validation
    data = enrich_schema(data, playbook=playbook)

    errors = validate(data)
    hard_errors = [e for e in errors if e.level == "ERROR"]

    return data, hard_errors


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch render and deploy sites to GitHub Pages"
    )
    parser.add_argument("leads_dir", nargs="?", default=str(config.LEADS_DIR),
                        help=f"Path to leads directory (default: {config.LEADS_DIR})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Render locally only, no deploy")
    parser.add_argument("--only", type=str, default="",
                        help="Categories: HOT, WARM, COOL (comma-separated). Default: all")
    parser.add_argument("--skip-errors", action="store_true",
                        help="Skip leads with validation errors instead of stopping")
    parser.add_argument("--playbook", type=str, default=None,
                        help="Path to playbook JSON file")
    parser.add_argument("--with-copy", action="store_true",
                        help="Generate AI copy (site + outreach) before rendering. Requires ANTHROPIC_API_KEY.")
    parser.add_argument("--enrich", action="store_true",
                        help="Enrich schemas with Claude CLI (Max subscription) before rendering")
    parser.add_argument("--enrich-model", default="opus", choices=["opus", "sonnet"],
                        help="Model for Claude CLI enrichment (default: opus)")
    parser.add_argument("--enrich-timeout", type=int, default=120,
                        help="Timeout per lead for Claude CLI enrichment (default: 120s)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Process only first N leads")
    args = parser.parse_args()

    leads_dir = os.path.abspath(args.leads_dir)
    if not os.path.isdir(leads_dir):
        print(f"Directory does not exist: {leads_dir}")
        sys.exit(1)

    categories = [c.strip().upper() for c in args.only.split(",") if c.strip()] or None
    template_dir = os.path.dirname(os.path.abspath(__file__))

    playbook = None
    if args.playbook:
        with open(args.playbook, "r", encoding="utf-8") as f:
            playbook = json.load(f)

    # Output directory for rendered sites
    build_dir = os.path.join(leads_dir, "_builds")
    os.makedirs(build_dir, exist_ok=True)

    # CSV report
    report_path = os.path.join(leads_dir, "_deploy_report.csv")

    # File logging
    log_path = os.path.join(leads_dir, f"_deploy_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        handlers=[logging.FileHandler(log_path), logging.StreamHandler()],
    )
    log = logging.getLogger(__name__)

    # Find leads
    leads = find_leads(leads_dir, categories)
    if not leads:
        print(f"No leads with schema_draft.json in {leads_dir}")
        sys.exit(1)

    if args.limit:
        leads = leads[: args.limit]

    print(f"""
{'='*64}
  BATCH DEPLOY - {len(leads)} leads
  Source:    {leads_dir}
  Build:     {build_dir}
  Deploy:    {'DRY RUN (local)' if args.dry_run else 'GitHub Pages'}
{'='*64}
""")

    results: List[Dict[str, str]] = []
    ok_count = 0
    err_count = 0
    skip_count = 0

    for i, lead in enumerate(leads, 1):
        schema_path = lead["schema_path"]
        category = lead["category"]
        folder_name = lead["folder_name"]

        print(f"\n[{i}/{len(leads)}] {category}/{folder_name}")
        print(f"  Schema: {schema_path}")

        # 1. Load and validate
        data, hard_errors = load_and_validate(schema_path, playbook=playbook)

        if data is None:
            print(f"  SKIP - invalid JSON: {hard_errors}")
            results.append({
                "category": category,
                "folder": folder_name,
                "slug": "",
                "status": "JSON_ERROR",
                "url": "",
                "error": str(hard_errors),
            })
            skip_count += 1
            continue

        if hard_errors:
            error_msgs = "; ".join(f"{e.field}: {e.msg}" for e in hard_errors)
            print(f"  ERRORS ({len(hard_errors)}):")
            for e in hard_errors:
                print(f"    {e}")

            if args.skip_errors:
                results.append({
                    "category": category,
                    "folder": folder_name,
                    "slug": data.get("slug", ""),
                    "status": "VALIDATION_ERROR",
                    "url": "",
                    "error": error_msgs,
                })
                skip_count += 1
                continue
            else:
                print(f"\n  Fix errors in {schema_path} or run with --skip-errors")
                sys.exit(1)

        slug = data.get("slug", "")
        if not slug:
            print("  SKIP - no slug field")
            results.append({
                "category": category,
                "folder": folder_name,
                "slug": "",
                "status": "NO_SLUG",
                "url": "",
                "error": "No slug",
            })
            skip_count += 1
            continue

        # 1b. Claude CLI enrichment (optional, $0 via Max subscription)
        if args.enrich and playbook:
            # Load raw data.json for competitor/review data
            data_json = None
            data_path = os.path.join(lead["folder"], "data.json")
            if os.path.exists(data_path):
                try:
                    with open(data_path, encoding="utf-8") as f:
                        data_json = json.load(f)
                except (json.JSONDecodeError, OSError):
                    pass

            # Load schema with meta fields for enrichment prompt
            with open(schema_path, encoding="utf-8") as f:
                raw_schema = json.load(f)

            prompt = build_enrichment_prompt(raw_schema, playbook, data_json)
            print(f"  Enriching with Claude CLI ({args.enrich_model})...")

            enriched_data = None
            for attempt in range(3):
                enriched_data = call_claude(prompt, model=args.enrich_model, timeout=args.enrich_timeout)
                if enriched_data:
                    break
                if attempt < 2:
                    wait = 5 * (attempt + 1)
                    print(f"  Retry {attempt + 1}/2 in {wait}s...")
                    time.sleep(wait)

            if enriched_data:
                raw_schema, outreach = merge_enriched(raw_schema, enriched_data)
                # Save enriched schema back
                with open(schema_path, "w", encoding="utf-8") as f:
                    json.dump(raw_schema, f, ensure_ascii=False, indent=2)
                # Save outreach
                if outreach:
                    outreach_path = os.path.join(lead["folder"], "_outreach.json")
                    with open(outreach_path, "w", encoding="utf-8") as f:
                        json.dump(outreach, f, ensure_ascii=False, indent=2)
                    # Build outreach HTML
                    site_url_placeholder = f"{config.DEPLOY_BASE_URL}/{slug}"
                    _build_outreach_html(data, outreach, site_url_placeholder, lead["folder"], playbook)
                # Reload enriched data for rendering
                data = {k: v for k, v in raw_schema.items() if not k.startswith("_")}
                data = enrich_schema(data, playbook=playbook)
                print(f"  Enriched: {', '.join(k for k in enriched_data if enriched_data.get(k))}")
            else:
                print("  Enrichment failed after 3 attempts")
                err_path = os.path.join(lead["folder"], "_enrich_error.txt")
                with open(err_path, "w", encoding="utf-8") as f:
                    f.write(f"Enrichment failed at {datetime.now(timezone.utc).isoformat()}\n")
                if not args.skip_errors:
                    log.error(f"  Enrichment failed for {category}/{folder_name}")

        # 1c. AI copy generation via API (optional)
        if args.with_copy and playbook:
            if not config.ANTHROPIC_API_KEY:
                print("  SKIP copy - ANTHROPIC_API_KEY not set")
            else:
                try:
                    print("  Generating AI copy...")
                    site_copy = generate_site_copy(data, playbook)
                    data.update(site_copy)
                    print(f"  Copy generated: {', '.join(site_copy.keys())}")
                except Exception as e:
                    print(f"  Copy generation failed: {e}")
                    if not args.skip_errors:
                        sys.exit(1)

        # 2. Render
        output_dir = os.path.join(build_dir, slug)
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir)

        data_dir = lead["folder"]
        rendered = render_templates(data, template_dir, output_dir, data_dir=data_dir)

        if not rendered:
            print("  ERROR - render failed")
            results.append({
                "category": category,
                "folder": folder_name,
                "slug": slug,
                "status": "RENDER_ERROR",
                "url": "",
                "error": "No rendered files",
            })
            err_count += 1
            continue

        print(f"  Rendered: {', '.join(rendered)}")

        # 3. Deploy
        site_url = ""
        if args.dry_run:
            site_url = f"{config.DEPLOY_BASE_URL}/{slug}"
            print(f"  DRY RUN - deploy skipped ({site_url})")
            status = "DRY_RUN"
        else:
            site_url = deploy_to_github(slug, output_dir)
            status = "DEPLOYED" if site_url else "DEPLOY_ERROR"

        # 4a. Update outreach HTML with real deploy URL (for --enrich)
        if args.enrich and site_url:
            outreach_path = os.path.join(lead["folder"], "_outreach.json")
            if os.path.exists(outreach_path):
                try:
                    with open(outreach_path, encoding="utf-8") as f:
                        outreach = json.load(f)
                    # Replace [DEMO_URL] placeholder with real URL
                    for key in ["whatsapp_initial", "email_initial", "followup_1", "followup_2", "followup_3"]:
                        if key in outreach and "[DEMO_URL]" in outreach[key]:
                            outreach[key] = outreach[key].replace("[DEMO_URL]", site_url)
                    with open(outreach_path, "w", encoding="utf-8") as f:
                        json.dump(outreach, f, ensure_ascii=False, indent=2)
                    # Rebuild outreach HTML with real URL
                    _build_outreach_html(data, outreach, site_url, lead["folder"], playbook)
                except Exception as e:
                    log.warning(f"  Outreach URL update failed: {e}")

        # 4b. Generate outreach via API (optional, after deploy so we have site_url)
        if args.with_copy and playbook and site_url and config.ANTHROPIC_API_KEY:
            try:
                print("  Generating outreach messages...")
                outreach = generate_outreach(data, playbook, site_url=site_url)
                # Save outreach JSON
                outreach_path = os.path.join(lead["folder"], "_outreach.json")
                with open(outreach_path, "w", encoding="utf-8") as f:
                    json.dump(outreach, f, ensure_ascii=False, indent=2)
                # Build outreach HTML from template
                _build_outreach_html(data, outreach, site_url, lead["folder"], playbook)
                print(f"  Outreach saved: {outreach_path}")
            except Exception as e:
                print(f"  Outreach generation failed: {e}")

        # Mark lead as "ready" in pipeline status
        if status in ("DEPLOYED", "DRY_RUN"):
            try:
                pipeline_status = load_status(Path(leads_dir))
                lead_key = f"{category}/{folder_name}"
                if lead_key not in pipeline_status["leads"]:
                    pipeline_status["leads"][lead_key] = {
                        "ready_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                        "contacted_date": None,
                        "channel": None,
                        "hook": None,
                        "niche": playbook.get("niche", "") if playbook else "",
                        "followups": [],
                        "outcome": None,
                        "deal_value": None,
                        "notes": "",
                    }
                    save_status(Path(leads_dir), pipeline_status)
                    log.info(f"  Pipeline: marked {lead_key} as ready")
            except Exception as e:
                log.warning(f"  Pipeline status update failed: {e}")

        results.append({
            "category": category,
            "folder": folder_name,
            "slug": slug,
            "status": status,
            "url": site_url,
            "error": "" if status != "DEPLOY_ERROR" else "Deploy failed",
        })

        if status in ("DEPLOYED", "DRY_RUN"):
            ok_count += 1
        else:
            err_count += 1

    # 4. Save CSV report
    with open(report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["category", "folder", "slug", "status", "url", "error"])
        writer.writeheader()
        writer.writerows(results)

    # 5. Summary
    print(f"""
{'='*64}
  DONE

  Success:    {ok_count}
  Errors:     {err_count}
  Skipped:    {skip_count}
  Total:      {len(leads)}

  Report:     {report_path}
  Builds:     {build_dir}
{'='*64}
""")

    # List deployed sites
    deployed = [r for r in results if r["status"] in ("DEPLOYED", "DRY_RUN")]
    if deployed:
        print("Sites:")
        for r in deployed:
            print(f"  [{r['category']}] {r['url']}")


if __name__ == "__main__":
    main()
