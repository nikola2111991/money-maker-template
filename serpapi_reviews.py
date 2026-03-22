#!/usr/bin/env python3
"""
SerpApi Google Maps Reviews - dopuna za scraper.py

Povlači SVE recenzije za lead (Places API vraća max 5).
Koristi se:
  1. Automatski iz scraper.py tokom batch run-a
  2. Standalone za postojeće leadove:
     python3 serpapi_reviews.py HOT/006_Auto_Servis_Petrovic_78pts

Env var: SERPAPI_KEY u .env ili ~/.zshrc
"""

import json
import os
import sys
import time
import logging
import glob as glob_mod
from typing import Dict, List, Optional
from datetime import datetime

import requests

from config import LEADS_DIR
from scraper import analyze_reviews

log = logging.getLogger("serpapi_reviews")

SERPAPI_URL = "https://serpapi.com/search.json"
MAX_PAGES = 50  # safety limit (~1000 reviews max)
REVIEWS_PER_PAGE = 20


def get_serpapi_key() -> str:
    """Ucitaj SERPAPI_KEY iz env vars."""
    key = os.environ.get("SERPAPI_KEY", "")
    if not key:
        # Probaj .env fajl u nj-space/
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        if k.strip() == "SERPAPI_KEY":
                            key = v.strip().strip('"').strip("'")
                            break
    return key


def fetch_all_reviews(place_id: str, api_key: str, language: str = "sr") -> List[Dict]:
    """
    Povuci SVE recenzije za place_id preko SerpApi.
    Vraca listu u formatu kompatibilnom sa scraper.py Lead.reviews:
      {"author": str, "rating": int, "text": str, "time": int, "relative_time": str}
    """
    all_reviews: List[Dict] = []
    params = {
        "api_key": api_key,
        "engine": "google_maps_reviews",
        "place_id": place_id,
        "hl": language,
        "sort_by": "newestFirst",
    }

    rate_limit_count = 0

    for page in range(MAX_PAGES):
        try:
            # num samo od druge stranice (kad imamo next_page_token)
            if "next_page_token" in params:
                params["num"] = REVIEWS_PER_PAGE

            resp = requests.get(SERPAPI_URL, params=params, timeout=30)

            if resp.status_code == 429:
                rate_limit_count += 1
                if rate_limit_count >= 5:
                    raise RuntimeError("SerpApi quota exhausted (5 consecutive 429s)")
                log.warning("SerpApi rate limit, cekam 10s...")
                time.sleep(10)
                continue

            if resp.status_code == 403:
                raise RuntimeError("SerpApi quota exhausted (403 Forbidden)")

            if resp.status_code != 200:
                log.error(f"SerpApi error {resp.status_code}: {resp.text[:200]}")
                break

            rate_limit_count = 0  # Reset on success

            data = resp.json()

            for r in data.get("reviews", []):
                # Konvertuj ISO date u unix timestamp
                unix_time = 0
                iso_date = r.get("iso_date", "")
                if iso_date:
                    try:
                        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
                        unix_time = int(dt.timestamp())
                    except Exception:
                        pass

                # Preferiraj original text ako postoji
                text = ""
                extracted = r.get("extracted_snippet", {})
                if isinstance(extracted, dict) and extracted.get("original"):
                    text = extracted["original"]
                if not text:
                    text = r.get("snippet", "")

                all_reviews.append(
                    {
                        "author": r.get("user", {}).get("name", ""),
                        "rating": r.get("rating", 5),
                        "text": text,
                        "time": unix_time,
                        "relative_time": r.get("date", ""),
                    }
                )

            # Paginacija
            pagination = data.get("serpapi_pagination", {})
            next_token = pagination.get("next_page_token")
            if not next_token:
                break

            params["next_page_token"] = next_token
            time.sleep(0.3)  # rate limit courtesy

        except requests.exceptions.Timeout:
            log.warning(f"SerpApi timeout na stranici {page + 1}, nastavljam...")
            time.sleep(5)
        except RuntimeError:
            raise  # Propagate quota exhaustion to caller
        except Exception as e:
            log.error(f"SerpApi greska: {e}")
            break

    return all_reviews


def enrich_lead_reviews(
    place_id: str,
    api_key: str,
    existing_reviews: Optional[List[Dict]] = None,
    language: str = "sr",
) -> List[Dict]:
    """
    Povuci sve recenzije. Ako SerpApi vrati manje od existing_reviews, zadrzi postojece.
    """
    serpapi_reviews = fetch_all_reviews(place_id, api_key, language=language)

    if not serpapi_reviews:
        log.warning(f"SerpApi vratio 0 recenzija za {place_id}, zadrzavam postojece")
        return existing_reviews or []

    existing_count = len(existing_reviews) if existing_reviews else 0
    if len(serpapi_reviews) < existing_count:
        log.warning(
            f"SerpApi vratio {len(serpapi_reviews)} < postojecih {existing_count}, zadrzavam postojece"
        )
        return existing_reviews or []

    log.info(f"SerpApi: {len(serpapi_reviews)} recenzija (bilo {existing_count})")
    return serpapi_reviews


# ============================================================
# STANDALONE MODE - za postojece leadove
# ============================================================


def find_lead_folder(query: str) -> Optional[str]:
    """Pronadji lead folder po query-ju (npr. 'HOT/006' ili '006')."""
    base = str(LEADS_DIR)

    # Direktan path
    full = os.path.join(base, query)
    if os.path.isdir(full):
        return full

    # HOT/006 format
    if "/" in query:
        cat, num = query.split("/", 1)
        cat_dir = os.path.join(base, cat)
        if os.path.isdir(cat_dir):
            for d in os.listdir(cat_dir):
                if d.startswith(num):
                    return os.path.join(cat_dir, d)

    # Samo broj - trazi u svim kategorijama
    for cat in ["HOT", "WARM", "COOL"]:
        cat_dir = os.path.join(base, cat)
        if os.path.isdir(cat_dir):
            for d in os.listdir(cat_dir):
                if d.startswith(query):
                    return os.path.join(cat_dir, d)

    return None


def update_schema_reviews(folder: str, api_key: str, language: str = "en") -> bool:
    """Azuriraj recenzije u schema_draft.json za dati lead folder."""
    # Pronadji schema fajl
    schema_path = None
    for name in ["schema_draft.json", "*.json"]:
        matches = glob_mod.glob(os.path.join(folder, name))
        for m in matches:
            if os.path.basename(m) not in (
                "review_analysis.json",
                "data.json",
                "competitor_report.json",
            ):
                schema_path = m
                break
        if schema_path:
            break

    if not schema_path:
        print(f"Nema schema JSON u {folder}")
        return False

    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)

    # Treba nam place_id - probaj iz data.json ili schema meta polja
    place_id = ""

    # 1. Iz data.json
    data_path = os.path.join(folder, "data.json")
    if os.path.exists(data_path):
        with open(data_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            place_id = data.get("place_id", "")

    # 2. Iz schema _place_id polja
    if not place_id:
        place_id = schema.get("_place_id", "")

    # 3. Iz google_maps_url (sadrzi CID, ne place_id - ne radi za SerpApi)
    if not place_id:
        print(f"Nema place_id za {folder}. Dodaj _place_id u schema ili data.json.")
        return False

    # Postojece recenzije iz schema
    existing = []
    for r in schema.get("reviews", []):
        existing.append(
            {
                "author": r.get("reviewer_name", ""),
                "rating": r.get("rating", 5),
                "text": r.get("text", ""),
                "time": 0,
                "relative_time": "",
            }
        )

    print(f"Povlacim recenzije za {schema.get('name_short', folder)}...")
    print(f"  place_id: {place_id}")
    print(f"  Postojece recenzije: {len(existing)}")

    reviews = enrich_lead_reviews(place_id, api_key, existing, language=language)

    if len(reviews) <= len(existing):
        print(f"  Nema novih recenzija ({len(reviews)} <= {len(existing)})")
        return False

    # Azuriraj schema recenzije (konvertuj u schema format)
    schema["reviews"] = []
    for r in reviews:
        schema["reviews"].append(
            {
                "reviewer_name": r["author"],
                "text": r["text"],
                "rating": r["rating"],
            }
        )

    # Azuriraj review count
    schema["review_count"] = len(reviews)

    # Sacuvaj schema
    with open(schema_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, ensure_ascii=False, indent=2)

    # Regenerisi review_analysis.json
    analysis = analyze_reviews(reviews)
    analysis_path = os.path.join(folder, "review_analysis.json")
    with open(analysis_path, "w", encoding="utf-8") as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2)

    print(f"  Azurirano: {len(reviews)} recenzija (bilo {len(existing)})")
    print(f"  Sacuvano u {schema_path}")
    return True


def batch_enrich(
    categories: Optional[List[str]] = None,
    min_reviews: int = 5,
    language: str = "en",
) -> int:
    """
    Batch SerpApi enrichment for all leads in given categories.
    Returns number of leads enriched.
    """
    if categories is None:
        categories = ["HOT", "WARM"]

    api_key = get_serpapi_key()
    if not api_key:
        print("SERPAPI_KEY nije setovan! Dodaj u .env ili ~/.zshrc")
        return 0

    base = str(LEADS_DIR)
    enriched = 0

    for cat in categories:
        cat_dir = os.path.join(base, cat)
        if not os.path.isdir(cat_dir):
            continue

        folders = sorted(
            [
                os.path.join(cat_dir, d)
                for d in os.listdir(cat_dir)
                if os.path.isdir(os.path.join(cat_dir, d))
            ]
        )

        if not folders:
            continue

        print(f"\n  {cat}: {len(folders)} leadova")

        for folder in folders:
            # Check review count from schema
            schema_path = os.path.join(folder, "schema_draft.json")
            if not os.path.exists(schema_path):
                continue

            with open(schema_path, "r", encoding="utf-8") as f:
                schema = json.load(f)

            review_count = schema.get("review_count", 0)
            if review_count < min_reviews:
                continue

            name = schema.get("name_short", os.path.basename(folder))
            print(f"    {name} ({review_count} reviews)...", end=" ", flush=True)

            success = update_schema_reviews(folder, api_key, language=language)
            if success:
                enriched += 1
                print("OK")
            else:
                print("skip")

            time.sleep(0.5)  # rate limit courtesy

    print(f"\n  SerpApi batch: {enriched} leadova obogaceno")
    return enriched


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="SerpApi Google Maps Reviews enrichment"
    )
    parser.add_argument("query", nargs="?", help="Lead folder query (e.g. HOT/006)")
    parser.add_argument(
        "--batch", action="store_true", help="Batch enrich all HOT+WARM leads"
    )
    parser.add_argument(
        "--only",
        default="HOT,WARM",
        help="Categories for batch mode (default: HOT,WARM)",
    )
    parser.add_argument(
        "--min-reviews",
        type=int,
        default=5,
        help="Min review count to enrich (default: 5)",
    )
    parser.add_argument(
        "--playbook",
        default=None,
        help="Playbook JSON (reads language setting)",
    )
    args = parser.parse_args()

    # Read language from playbook if provided
    language = "en"
    if args.playbook and os.path.exists(args.playbook):
        with open(args.playbook, "r", encoding="utf-8") as f:
            pb = json.load(f)
            language = pb.get("language", "en")

    if args.batch:
        categories = [c.strip() for c in args.only.split(",")]
        print(f"SerpApi batch enrichment: {', '.join(categories)} (lang={language})")
        batch_enrich(
            categories=categories, min_reviews=args.min_reviews, language=language
        )
        return

    if not args.query:
        parser.print_help()
        sys.exit(1)

    api_key = get_serpapi_key()
    if not api_key:
        print("SERPAPI_KEY nije setovan! Dodaj u .env ili ~/.zshrc:")
        print('  SERPAPI_KEY="tvoj_kljuc"')
        sys.exit(1)

    folder = find_lead_folder(args.query)

    if not folder:
        print(f"Lead folder '{args.query}' nije pronadjen u {LEADS_DIR}")
        sys.exit(1)

    print(f"Lead folder: {folder}\n")
    success = update_schema_reviews(folder, api_key, language=language)

    if success:
        print("\nGotovo. Pokreni ponovo render.py za azuriran sajt.")
    else:
        print("\nNista nije promenjeno.")


if __name__ == "__main__":
    main()
