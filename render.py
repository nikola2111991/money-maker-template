"""
render.py - Validacija JSON + Jinja2 renderovanje
Usage: python render.py data.json output_dir/
"""
import json, sys, os, re, shutil
import urllib.parse
from datetime import datetime
from jinja2 import Environment, FileSystemLoader, Undefined
from utils import cyr_to_lat, has_cyrillic, strip_diacritics

# --- KONFIGURACIJA ---

REQUIRED_FIELDS = [
    "slug", "name", "name_short", "owner", "owner_short",
    "city", "address",
    "rating", "review_count", "hero_headline", "hero_subtitle"
]

# phone/phone_display are NOT in REQUIRED_FIELDS (validation level).
# Leadovi bez mobilnog se preskacu u pipeline-u.

REQUIRED_ARRAYS = {
    "benefits": {"min": 3, "fields": ["title", "description"]},
    "services": {"min": 1, "fields": ["title", "description"]},
    "problems": {"min": 3, "fields": ["title", "treatment", "description"]},
    "reviews": {"min": 1, "fields": ["reviewer_name", "text"]},
    "faq": {"min": 3, "fields": ["question", "answer"]},
}

TEMPLATES = ["index.html", "services.html", "about.html", "contact.html", "404.html"]

PLACEHOLDERS = ["???", "_POPUNI_", "POPUNI", "_PLACEHOLDER_", "_FILL_IN_", "FILL"]

# --- VALIDACIJA ---

class ValidationError:
    def __init__(self, field, msg, level="ERROR"):
        self.field = field
        self.msg = msg
        self.level = level

    def __str__(self):
        icon = "❌" if self.level == "ERROR" else "⚠️"
        return f"  {icon} {self.field}: {self.msg}"


def is_placeholder(val):
    """Proverava da li je vrednost placeholder koji treba popuniti.

    Tekstovi duzi od 50 karaktera se smatraju pravim sadrzajem
    cak i ako sadrze '???' - to je obicna interpunkcija u recenzijama.
    """
    if not isinstance(val, str):
        return False
    val_stripped = val.strip()
    if val_stripped in PLACEHOLDERS:
        return True
    # Duzi tekst nije placeholder, cak i ako sadrzi ??? u sredini
    if len(val_stripped) > 50:
        return False
    for p in PLACEHOLDERS:
        if p in val_stripped:
            return True
    return False


def is_valid_url(val):
    """Proverava da li string liči na validan URL ili relativnu putanju."""
    if not isinstance(val, str) or not val.strip():
        return True  # prazno je OK (opciono polje)
    val = val.strip()
    # Dozvoli relativne putanje (photos/photo_01.jpg) - ali blokiraj path traversal
    if not val.startswith('http'):
        if '..' in val or val.startswith('/'):
            return False
        if '/' in val and re.match(r'^[\w\-./]+$', val):
            return True
        return False
    return bool(re.match(r'^https?://', val))


def validate(data):
    errors = []

    # 1. Obavezna polja
    for f in REQUIRED_FIELDS:
        val = data.get(f)
        if val is None or val == "":
            errors.append(ValidationError(f, "nedostaje ili nije popunjeno"))
        elif is_placeholder(val):
            errors.append(ValidationError(f, f"sadrži placeholder: '{val}'"))

    # 2. Obavezni nizovi
    for arr_name, rules in REQUIRED_ARRAYS.items():
        arr = data.get(arr_name, [])
        if not isinstance(arr, list):
            errors.append(ValidationError(arr_name, "mora biti niz []"))
            continue
        if len(arr) < rules["min"]:
            errors.append(ValidationError(arr_name, f"treba minimum {rules['min']}, ima {len(arr)}"))
        for i, item in enumerate(arr):
            if not isinstance(item, dict):
                continue
            for field in rules["fields"]:
                val = item.get(field)
                if val is None or val == "":
                    errors.append(ValidationError(
                        f"{arr_name}[{i}].{field}",
                        "nedostaje ili nije popunjeno"
                    ))
                elif is_placeholder(val):
                    errors.append(ValidationError(
                        f"{arr_name}[{i}].{field}",
                        f"sadrži placeholder: '{val}'"
                    ))

    # 2b. Schema completeness - warn about fields templates expect but scraper might miss
    KNOWN_FIELDS = {
        # Required
        "slug", "name", "name_short", "owner", "owner_short",
        "city", "district", "address", "phone", "phone_display",
        "rating", "review_count", "hero_headline", "hero_subtitle",
        # Arrays
        "benefits", "services", "problems", "reviews", "faq", "hours", "core_values",
        # Optional strings
        "email", "facebook", "instagram", "google_maps_url",
        "hero_image", "years_established", "founded", "specialization",
        "about_story", "about_image", "about_headline", "about_paragraphs",
        "about_blockquote", "about_stats", "name_genitive", "name_locative",
        "benefits_headline", "benefits_subtitle",
        "services_subtitle", "about_subtitle", "contact_subtitle",
        "is_city_level", "google_maps_embed_url", "base_url",
        # Auto-specific
        "brands", "certifications",
        # Theme
        "theme",
        # i18n / generalized
        "i18n", "phone_prefix", "schema_type", "country_code",
    }
    unknown_fields = [k for k in data.keys() if k not in KNOWN_FIELDS and not k.startswith("_")]
    if unknown_fields:
        errors.append(ValidationError("schema", f"nepoznata polja (ignorisana): {', '.join(unknown_fields)}", "WARN"))

    # 3. Opciona polja - samo upozorenja
    optional_checks = [
        ("google_maps_url", "nema link ka Google Maps"),
        ("email", "nema email adresu"),
        ("about_story", "nema priču na O nama stranici"),
        ("facebook", "nema Facebook stranicu"),
        ("instagram", "nema Instagram stranicu"),
    ]
    for f, msg in optional_checks:
        val = data.get(f)
        if not val or is_placeholder(val):
            errors.append(ValidationError(f, msg, "WARN"))

    # 4. Format provere
    mobilni = data.get("phone", "")
    if not mobilni:
        errors.append(ValidationError("phone", "prazan - lead bez mobilnog, preskoci", "WARN"))
    elif not re.match(r'^\+?\d[\d\s\-/]{6,}$', mobilni):
        errors.append(ValidationError("phone", f"neispravan format: '{mobilni}'"))

    rating = data.get("rating", 0)
    if isinstance(rating, str):
        errors.append(ValidationError("rating", f"mora biti broj, ne string: '{rating}'"))
    elif isinstance(rating, (int, float)) and (rating < 0 or rating > 5):
        errors.append(ValidationError("rating", f"mora biti 0-5, dobio: {rating}"))

    recenzija_count = data.get("review_count", 0)
    if isinstance(recenzija_count, str):
        errors.append(ValidationError("review_count", f"mora biti broj, ne string: '{recenzija_count}'"))

    # 5. URL format provere
    url_fields = ["google_maps_url", "website", "facebook", "instagram", "hero_image"]
    for f in url_fields:
        val = data.get(f, "")
        if val and not is_valid_url(val):
            errors.append(ValidationError(f, f"ne liči na validan URL: '{val[:50]}'", "WARN"))

    # 6. Recenzija rating provera
    for i, rec in enumerate(data.get("reviews", [])):
        if isinstance(rec, dict):
            r = rec.get("rating")
            if r is not None:
                if not isinstance(r, (int, float)):
                    errors.append(ValidationError(f"recenzije[{i}].rating", f"mora biti broj: '{r}'", "WARN"))
                elif r < 1 or r > 5:
                    errors.append(ValidationError(f"recenzije[{i}].rating", f"mora biti 1-5, dobio: {r}", "WARN"))

    # 7. Slug format provera
    slug = data.get("slug", "")
    if slug and not re.match(r'^[a-z0-9-]+$', slug):
        errors.append(ValidationError("slug", f"sme sadržati samo mala slova, cifre i crtice: '{slug}'"))

    # 8. Em dash auto-fix - zameni em dash tackom, capitalize posle
    def _fix_em_dash_str(s):
        import re as _re
        # " — " ili "— " ili " —" ili "—" -> ". "
        s = _re.sub(r'\s*\u2014\s*', '. ', s)
        # Capitalize slovo posle ". " gde je malo
        s = _re.sub(r'\. ([a-zčćšžđ])', lambda m: '. ' + m.group(1).upper(), s)
        return s
    def _fix_em_dash(obj):
        if isinstance(obj, str):
            return _fix_em_dash_str(obj)
        elif isinstance(obj, dict):
            return {k: _fix_em_dash(v) if not k.startswith("_") and k not in URL_FIELDS else v for k, v in obj.items()}
        elif isinstance(obj, list):
            return [_fix_em_dash(item) for item in obj]
        return obj
    data.update(_fix_em_dash(data))

    return errors


# --- ENRICHMENT ---
# Auto-enrich schema with derivable data (images, vrednosti, map URL, etc.)





def _optimize_unsplash_url(url: str, width: int = 600) -> str:
    """Add WebP format and size params to Unsplash URLs."""
    if not url or "unsplash.com" not in url:
        return url
    base = url.split("?")[0]
    return f"{base}?auto=format&fm=webp&fit=crop&w={width}&q=80"


def enrich_schema(data, json_dir=None, playbook=None):
    """Auto-enrich schema with missing but derivable fields.

    Adds: service images, Google Maps embed URL, about paragraphs, theme.
    Does not overwrite fields that already have content.
    All niche-specific data comes from playbook, never hardcoded.

    json_dir: path to directory containing schema JSON (for scanning photos/).
    playbook: dict with niche playbook config (required for image/problem fallbacks).
    """
    # Inject playbook data into schema
    if playbook:
        if 'i18n' not in data:
            data['i18n'] = playbook.get('i18n', {})
        if 'phone_prefix' not in data:
            data['phone_prefix'] = playbook.get('phone_prefix', '')
        if 'schema_type' not in data:
            data['schema_type'] = playbook.get('schema_type', 'LocalBusiness')
        if 'country_code' not in data:
            data['country_code'] = playbook.get('country_code', '')
        data['_transliterate'] = playbook.get('text_processing', {}).get('transliterate', False)
        if playbook.get('formspree_id') and 'formspree_id' not in data:
            data['formspree_id'] = playbook['formspree_id']

    # Image config from playbook (required)
    image_map = playbook.get('image_map', []) if playbook else []
    cycle_images = playbook.get('cycle_images', []) if playbook else []
    default_img = _optimize_unsplash_url(playbook.get('default_image', '')) if playbook else ''

    # 0a. Fallback vlasnik - zameni _POPUNI_ placeholder
    vlasnik_bad = is_placeholder(data.get('owner', '')) or not data.get('owner')
    kratko_bad = is_placeholder(data.get('owner_short', '')) or not data.get('owner_short')
    if vlasnik_bad or kratko_bad:
        naziv = data.get('name', '')
        # Try to extract owner name from business name
        words = naziv.split()
        owner_found = False
        biz_words = set(playbook.get('business_type_words', [])) if playbook else set()
        if len(words) >= 2:
            last = words[-1]
            servis_words = biz_words or {"service", "center", "plus", "pro", "express"}
            if last[0:1].isupper() and len(last) >= 4 and last.lower() not in servis_words:
                if vlasnik_bad:
                    data['owner'] = last
                if kratko_bad:
                    data['owner_short'] = last
                owner_found = True
        if not owner_found:
            if vlasnik_bad:
                data['owner'] = data.get('name_short', 'Owner')
            if kratko_bad:
                data['owner_short'] = 'Owner'

    # 0b. Fallback problemi from playbook
    problemi = data.get('problems', [])
    if len(problemi) < 3 and playbook and playbook.get('default_problems'):
        pb_problems = playbook['default_problems']
        seen = {s.get('title', '') for s in problemi}
        for dp in pb_problems:
            if len(problemi) >= 3:
                break
            title = dp.get('title', '')
            if title not in seen:
                problemi.append({
                    "title": title,
                    "treatment": dp.get('treatment', ''),
                    "description": dp.get('description', ''),
                    "duration": dp.get('duration', ''),
                    "price": dp.get('price', ''),
                    "recovery": dp.get('recovery', ''),
                })
                seen.add(title)
        data['problems'] = problemi

    # 0c. Collect local photos - scan photos/ directory + schema references
    _local_photos = []
    # Prvo skeniraj photos/ folder ako postoji
    if json_dir:
        photos_dir = os.path.join(json_dir, 'photos')
        if os.path.isdir(photos_dir):
            img_exts = {'.jpg', '.jpeg', '.png', '.webp'}
            for fname in sorted(os.listdir(photos_dir)):
                if os.path.splitext(fname)[1].lower() in img_exts:
                    rel_path = f'photos/{fname}'
                    if rel_path not in _local_photos:
                        _local_photos.append(rel_path)
    # Dopuni iz schema polja (ako ima referenci koje nisu u photos/ folderu)
    hero = data.get('hero_image', '')
    if hero and not hero.startswith('http') and hero not in _local_photos:
        _local_photos.append(hero)
    for u in data.get('services', []):
        s = u.get('image_url', '')
        if s and not s.startswith('http') and s not in _local_photos:
            _local_photos.append(s)
    for b in data.get('benefits', []):
        s = b.get('image_url', '')
        if s and not s.startswith('http') and s not in _local_photos:
            _local_photos.append(s)

    # 0d. hero_image - fallback to first local photo if empty
    if not data.get('hero_image') and _local_photos:
        data['hero_image'] = _local_photos[0]

    # 0e. about_image - fallback to local photo if empty
    if not data.get('about_image'):
        if _local_photos:
            data['about_image'] = _local_photos[0]

    # 1. Service images - local photos 1:1, Unsplash za ostatak (nikad ponavljaj istu)
    usluge = data.get('services', [])
    _used_local = set()  # prati koje lokalne slike su vec dodeljene
    for i, usluga in enumerate(usluge):
        existing = usluga.get('image_url', '')
        if existing and not existing.startswith('http'):
            _used_local.add(existing)
            continue  # lokalna slika vec postavljena - ne diraj
        if not existing or existing == default_img:
            # Probaj lokalnu sliku koja NIJE vec koriscena
            assigned_local = False
            if _local_photos:
                for lp in _local_photos:
                    if lp not in _used_local:
                        usluga['image_url'] = lp
                        _used_local.add(lp)
                        assigned_local = True
                        break
            # Ako nema vise unikatnih lokalnih - Unsplash po tipu usluge
            if not assigned_local:
                naziv = strip_diacritics(usluga.get('title', '').lower())
                matched = False
                for keyword_pair in image_map:
                    keyword = keyword_pair[0] if isinstance(keyword_pair, (list, tuple)) else keyword_pair
                    photo_id = keyword_pair[1] if isinstance(keyword_pair, (list, tuple)) else keyword_pair
                    if keyword in naziv:
                        usluga['image_url'] = _optimize_unsplash_url(f'https://images.unsplash.com/{photo_id}')
                        matched = True
                        break
                if not matched and cycle_images:
                    usluga['image_url'] = _optimize_unsplash_url(f'https://images.unsplash.com/{cycle_images[i % len(cycle_images)]}')
                elif not matched and default_img:
                    usluga['image_url'] = default_img

    # 1b. Benefit images - local photos 1:1, Unsplash za ostatak
    benefiti = data.get('benefits', [])
    for i, benefit in enumerate(benefiti):
        existing = benefit.get('image_url', '')
        if existing and not existing.startswith('http'):
            _used_local.add(existing)
            continue  # lokalna slika vec postavljena - ne diraj
        if not existing:
            assigned_local = False
            if _local_photos:
                for lp in _local_photos:
                    if lp not in _used_local:
                        benefit['image_url'] = lp
                        _used_local.add(lp)
                        assigned_local = True
                        break
            if not assigned_local and cycle_images:
                benefit['image_url'] = _optimize_unsplash_url(f'https://images.unsplash.com/{cycle_images[(i + 3) % len(cycle_images)]}')
            elif not assigned_local and default_img:
                benefit['image_url'] = default_img

    # 2. Google Maps embed URL
    adresa = data.get('address', '')
    if adresa and not data.get('google_maps_embed_url'):
        grad = data.get('city', '')
        q = f'{adresa}' if grad.lower() in adresa.lower() else f'{adresa}, {grad}'
        data['google_maps_embed_url'] = f'https://maps.google.com/maps?q={urllib.parse.quote(q)}&output=embed&z=16'

    # 3. About paragraphs - split story into array for template
    prica = data.get('about_story', '')
    if prica and not data.get('about_paragraphs'):
        paragraphs = [p.strip() for p in prica.split('\n\n') if p.strip()]
        if len(paragraphs) == 1 and len(prica) > 200:
            sentences = prica.replace('. ', '.\n').split('\n')
            mid = len(sentences) // 2
            paragraphs = [' '.join(sentences[:mid]).strip(), ' '.join(sentences[mid:]).strip()]
        data['about_paragraphs'] = [p for p in paragraphs if p]

    # 4. Theme - boje, fontovi, favicon na osnovu tipa servisa
    if not data.get('theme'):
        data['theme'] = _pick_theme(data, playbook=playbook)

    return data


# --- THEME SYSTEM ---

THEMES = {
    'trusted': {
        'name': 'trusted',
        'primary': '#1B2A4A', 'primary_light': '#2d4373',
        'accent': '#4A90A4', 'accent_light': '#5fb3c9',
        'dark': '#0f1923', 'favicon_fill': '%231B2A4A',
        'bg_soft': '#f3f5f8', 'bg_alt': '#e8ecf2',
        'primary_rgb': '27,42,74', 'accent_rgb': '74,144,164',
        'card_radius': '16px', 'btn_radius': '10px',
        'font_display': "'Libre Baskerville',serif",
        'font_body': "'Source Sans 3',sans-serif",
        'font_url': 'https://fonts.googleapis.com/css2?family=Libre+Baskerville:wght@700&family=Source+Sans+3:wght@400;600;700&display=swap',
    },
    'modern': {
        'name': 'modern',
        'primary': '#2C3E50', 'primary_light': '#3d5a80',
        'accent': '#00BCD4', 'accent_light': '#26c6da',
        'dark': '#1a252f', 'favicon_fill': '%232C3E50',
        'bg_soft': '#f3f7fa', 'bg_alt': '#e5eef5',
        'primary_rgb': '44,62,80', 'accent_rgb': '0,188,212',
        'card_radius': '14px', 'btn_radius': '8px',
        'font_display': "'Space Grotesk',sans-serif",
        'font_body': "'Inter',sans-serif",
        'font_url': 'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700&family=Space+Grotesk:wght@600;700&display=swap',
    },
    'performance': {
        'name': 'performance',
        'primary': '#1A1A2E', 'primary_light': '#2d2d4a',
        'accent': '#E74C3C', 'accent_light': '#ff6b5b',
        'dark': '#0d0d17', 'favicon_fill': '%231A1A2E',
        'bg_soft': '#f5f5f7', 'bg_alt': '#eaeaef',
        'primary_rgb': '26,26,46', 'accent_rgb': '231,76,60',
        'card_radius': '10px', 'btn_radius': '6px',
        'font_display': "'Sora',sans-serif",
        'font_body': "'DM Sans',sans-serif",
        'font_url': 'https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&family=Sora:wght@600;700&display=swap',
    },
    'family': {
        'name': 'family',
        'primary': '#2C3E50', 'primary_light': '#3d5a80',
        'accent': '#27AE60', 'accent_light': '#2ecc71',
        'dark': '#1a252f', 'favicon_fill': '%232C3E50',
        'bg_soft': '#f3faf5', 'bg_alt': '#e5f5eb',
        'primary_rgb': '44,62,80', 'accent_rgb': '39,174,96',
        'card_radius': '20px', 'btn_radius': '14px',
        'font_display': "'Nunito',sans-serif",
        'font_body': "'Quicksand',sans-serif",
        'font_url': 'https://fonts.googleapis.com/css2?family=Nunito:wght@600;700&family=Quicksand:wght@400;500;700&display=swap',
    },
    'specialist': {
        'name': 'specialist',
        'primary': '#1A237E', 'primary_light': '#283593',
        'accent': '#FFA000', 'accent_light': '#ffb300',
        'dark': '#0d1147', 'favicon_fill': '%231A237E',
        'bg_soft': '#f3f4fa', 'bg_alt': '#e8eaf6',
        'primary_rgb': '26,35,126', 'accent_rgb': '255,160,0',
        'card_radius': '12px', 'btn_radius': '8px',
        'font_display': "'Outfit',sans-serif",
        'font_body': "'DM Sans',sans-serif",
        'font_url': 'https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&family=Outfit:wght@600;700&display=swap',
    },
    'clean': {
        'name': 'clean',
        'primary': '#006D77', 'primary_light': '#0097a7',
        'accent': '#009688', 'accent_light': '#26a69a',
        'dark': '#003d42', 'favicon_fill': '%23006D77',
        'bg_soft': '#f2f8f8', 'bg_alt': '#e0f2f1',
        'primary_rgb': '0,109,119', 'accent_rgb': '0,150,136',
        'card_radius': '14px', 'btn_radius': '8px',
        'font_display': "'Fraunces',serif",
        'font_body': "'Work Sans',sans-serif",
        'font_url': 'https://fonts.googleapis.com/css2?family=Fraunces:wght@600;700&family=Work+Sans:wght@400;500;700&display=swap',
    },
}


def _pick_theme(data, playbook=None):
    """Bira temu na osnovu tipa servisa i karakteristika."""
    theme_rules = playbook.get('theme_rules', {}) if playbook else {}
    spec = strip_diacritics(data.get('specialization', '').lower())
    naziv = strip_diacritics(data.get('name', '').lower())
    usluge_text = ' '.join(strip_diacritics(u.get('title', '').lower()) for u in data.get('services', []))

    # Use playbook theme_rules if available
    if theme_rules:
        combined = f'{spec} {naziv} {usluge_text}'
        for theme_name, keywords in theme_rules.items():
            if theme_name in THEMES and any(kw in combined for kw in keywords):
                return THEMES[theme_name]
    else:
        # Performance: tuning, chip, sport
        if any(kw in spec or kw in naziv for kw in ['tuning', 'chip', 'sport', 'wrapping', 'detailing']):
            return THEMES['performance']

        # Specialist: brendirani servisi
        if any(kw in naziv for kw in ['bmw', 'mercedes', 'audi', 'vw', 'volkswagen', 'ovlascen']):
            return THEMES['specialist']

        # Family: porodični
        if 'porodic' in naziv or 'family' in naziv:
            return THEMES['family']

        # Clean: pranje, poliranje
        if any(kw in naziv or kw in usluge_text for kw in ['pranj', 'polir', 'keram', 'detailing']):
            return THEMES['clean']

    # Cycle remaining
    slug = data.get('slug', data.get('name', ''))
    remaining = ['trusted', 'modern', 'family', 'clean']
    idx = sum(ord(c) for c in slug) % len(remaining)
    return THEMES[remaining[idx]]


# --- RENDEROVANJE ---

def escape_html_chars(text):
    """Escape HTML specijalne karaktere u stringu.
    
    Sprečava lomljenje HTML atributa (content="...") kad tekst sadrži navodnike,
    i sprečava XSS kad tekst sadrži <script> ili slično.
    """
    if not isinstance(text, str):
        return text
    text = text.replace('&', '&amp;')
    text = text.replace('<', '&lt;')
    text = text.replace('>', '&gt;')
    text = text.replace('"', '&quot;')
    return text


# Polja koja sadrže URL-ove - ne escape-ujemo & u njima jer bi pokvarilo linkove
URL_FIELDS = {
    'google_maps_url', 'google_maps_embed_url', 'website', 'facebook', 'instagram',
    'hero_image', 'image_url', 'slug', 'og_url', 'font_url', 'base_url'
}


def sanitize_tema(tema):
    """Validate tema dict values only contain safe CSS characters.

    Allows alphanumeric, #, -, ., spaces, commas, quotes, parentheses.
    Strips any value that contains characters outside this set.
    Preskace font_url i favicon_fill jer sadrze URL/percent-encoded karaktere.
    """
    if not isinstance(tema, dict):
        return tema
    # Polja koja sadrze URL-ove ili percent-encoded vrednosti - ne sanitizuj
    SKIP_FIELDS = {'font_url', 'favicon_fill'}
    safe_pattern = re.compile(r"^[a-zA-Z0-9#\-., '\"()]+$")
    sanitized = {}
    for k, v in tema.items():
        if isinstance(v, str):
            if k in SKIP_FIELDS or safe_pattern.match(v):
                sanitized[k] = v
            else:
                sanitized[k] = ''
        else:
            sanitized[k] = v
    return sanitized


def sanitize_data(data, _depth=0):
    """Sanitizuj sve string vrednosti u data dict-u pre renderovanja.

    1. Transliteruj ćirilicu u latinicu (adresa, recenzije, itd.) - ako je _transliterate True
    2. Escape HTML specijalne karaktere (&, <, >, ")
    3. Preskoči URL polja (& je validan u URL-ovima)
    4. Sanitizuje tema dict - dozvoljava samo safe CSS karaktere
    """
    if _depth > 5:  # Zaštita od beskonačne rekurzije
        return data
    # Check transliteration setting at top level
    should_transliterate = data.get('_transliterate', True) if isinstance(data, dict) and _depth == 0 else True
    if isinstance(data, dict):
        result = {}
        # Propagate _transliterate flag
        if '_transliterate' in data:
            should_transliterate = data['_transliterate']
        for k, v in data.items():
            # Sanitize theme dict - validate CSS-safe values
            if k == 'theme':
                result[k] = sanitize_tema(v)
                continue
            if isinstance(v, str):
                if k not in URL_FIELDS:
                    if should_transliterate and has_cyrillic(v):
                        v = cyr_to_lat(v)
                    v = escape_html_chars(v)
                else:
                    # URL polja: ne escape-uj, ne transliteruj
                    pass
            elif isinstance(v, (dict, list)):
                # Pass transliterate flag down
                if isinstance(v, dict) and '_transliterate' not in v:
                    v['_transliterate'] = should_transliterate
                v = sanitize_data(v, _depth + 1)
            result[k] = v
        return result
    elif isinstance(data, list):
        return [sanitize_data(item, _depth + 1) if isinstance(item, (dict, list, str))
                else item for item in data]
    elif isinstance(data, str):
        # At non-root level, transliteration is always on (default behavior)
        if has_cyrillic(data):
            data = cyr_to_lat(data)
        return escape_html_chars(data)
    return data


class SilentUndefined(Undefined):
    """Umesto greške, vrati prazan string za nedefinisane varijable."""
    def __str__(self):
        return ""
    def __iter__(self):
        return iter([])
    def __bool__(self):
        return False
    def __getattr__(self, name):
        return self


def ascii_safe_html(html: str) -> str:
    """Convert all non-ASCII characters to HTML numeric entities.

    This ensures the deployed HTML displays correctly regardless of
    how the deploy pipeline handles encoding.
    Characters inside Jinja blocks are already resolved at this point.

    IMPORTANT: Skips content inside <script type="application/ld+json"> blocks,
    because JSON-LD parsers expect raw JSON, not HTML entities.
    """
    # Split around JSON-LD script blocks to avoid encoding their content
    parts = re.split(r'(<script\s+type=["\']application/ld\+json["\']>)(.*?)(</script>)', html, flags=re.DOTALL)
    result = []
    for i, part in enumerate(parts):
        if i % 4 == 2:
            # This is JSON-LD content - use JSON Unicode escapes instead
            json_chars = []
            for ch in part:
                if ord(ch) > 127:
                    json_chars.append(f'\\u{ord(ch):04x}')
                else:
                    json_chars.append(ch)
            result.append(''.join(json_chars))
        else:
            # Regular HTML + script tags - pass through as UTF-8
            # charset=UTF-8 is declared in <head>, modern browsers handle it correctly
            result.append(part)
    return ''.join(result)


def _json_val(s):
    """Convert HTML-escaped string to JSON-safe string for use in JSON-LD blocks.

    sanitize_data() converts " to &quot; which is correct for HTML attributes
    but breaks JSON-LD <script> blocks where raw JSON is expected.
    """
    if not isinstance(s, str):
        return s if s is not None else ''
    # Reverse HTML escaping
    s = s.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')
    # JSON-escape: backslash and double quotes
    s = s.replace('\\', '\\\\').replace('"', '\\"')
    # Escape </ to prevent </script> injection in JSON-LD blocks
    s = s.replace('</', '<\\/')
    return s


def _flatten_image_refs(data):
    """Izvuci sve image reference iz schema data za proveru."""
    refs = []
    for field in ('hero_image', 'about_image'):
        v = data.get(field, '')
        if v:
            refs.append(v)
    for arr in ('services', 'benefits'):
        for item in data.get(arr, []):
            v = item.get('image_url', '')
            if v:
                refs.append(v)
    return refs


def render_templates(data, template_dir, output_dir, data_dir=None):
    env = Environment(
        loader=FileSystemLoader(template_dir),
        undefined=SilentUndefined,
        autoescape=False
    )
    env.filters['json_val'] = _json_val

    # Generate base_url from slug if not already set
    if data.get("slug") and not data.get("base_url"):
        data["base_url"] = f"{DEPLOY_BASE_URL}/{data['slug']}"

    # Sanitizuj podatke pre renderovanja (Cyrillic→Latin, HTML escape)
    data = sanitize_data(data)

    os.makedirs(output_dir, exist_ok=True)
    rendered = []

    for tpl_name in TEMPLATES:
        tpl_path = os.path.join(template_dir, tpl_name)
        if not os.path.exists(tpl_path):
            print(f"  ⭕ {tpl_name} - šablon ne postoji, preskačem")
            continue

        try:
            template = env.get_template(tpl_name)
            html = template.render(data=data)
        except Exception as e:
            print(f"  ❌ {tpl_name} - greška pri renderovanju: {e}")
            continue
        # FIX: Convert all non-ASCII to HTML entities for deploy safety
        html = ascii_safe_html(html)
        out_path = os.path.join(output_dir, tpl_name)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
        rendered.append(tpl_name)
        print(f"  ✅ {tpl_name} - renderovano ({len(html)} karaktera)")

    # Kopiraj style.css
    css_src = os.path.join(template_dir, "style.css")
    if os.path.exists(css_src):
        shutil.copy2(css_src, os.path.join(output_dir, "style.css"))
        print(f"  ✅ style.css - kopirano")

    # Kopiraj main.js
    js_src = os.path.join(template_dir, "main.js")
    if os.path.exists(js_src):
        shutil.copy2(js_src, os.path.join(output_dir, "main.js"))
        print(f"  ✅ main.js - kopirano")

    # Kopiraj photos/ ako postoji u data_dir
    has_local_refs = any('photos/' in str(v) for v in _flatten_image_refs(data))
    if data_dir:
        photos_src = os.path.join(data_dir, "photos")
        if os.path.isdir(photos_src):
            photos_dst = os.path.join(output_dir, "photos")
            shutil.copytree(photos_src, photos_dst, dirs_exist_ok=True)
            photo_count = len([f for f in os.listdir(photos_dst) if f.endswith(('.jpg', '.jpeg', '.png', '.webp'))])
            print(f"  ✅ photos/ - kopirano ({photo_count} slika)")
        elif has_local_refs:
            print(f"  ⚠️  Schema referencira photos/ ali folder ne postoji: {photos_src}")

    return rendered


# --- DEPLOY ---

from config import DEPLOY_REPO, DEPLOY_BASE_URL

GITHUB_PAGES_REPO = str(DEPLOY_REPO)
GITHUB_PAGES_BASE = DEPLOY_BASE_URL


def deploy_to_github(slug: str, output_dir: str) -> str:
    """Deploy renderovani sajt na GitHub Pages.

    Kopira fajlove u auto-demos/[slug]/, commit-uje i push-uje.
    Returns: live URL ako uspešno, prazan string ako ne.
    """
    import subprocess
    import shutil

    repo_dir = GITHUB_PAGES_REPO
    site_dir = os.path.join(repo_dir, slug)

    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        print(f"  ❌ Repo ne postoji: {repo_dir}")
        print(f"     Pokreni: git clone https://github.com/nikola2111991/auto-demos.git ~/Documents/auto-demos")
        return ""

    print(f"\n🚀 Deploy na GitHub Pages: {slug}")

    # 1. Pull latest
    try:
        subprocess.run(["git", "pull", "--rebase", "--quiet"], cwd=repo_dir, timeout=30, capture_output=True)
    except Exception:
        pass  # OK ako nema promena

    # 2. Kopiraj renderovane fajlove u slug folder
    if os.path.isdir(site_dir):
        shutil.rmtree(site_dir)
    shutil.copytree(output_dir, site_dir)
    print(f"  📁 Kopirano u {site_dir}")

    # 3. Git add + commit + push
    try:
        subprocess.run(["git", "add", slug], cwd=repo_dir, timeout=15)
        result = subprocess.run(
            ["git", "commit", "-m", f"deploy {slug}"],
            cwd=repo_dir, timeout=15, capture_output=True, text=True
        )
        if result.returncode != 0 and "nothing to commit" in result.stdout + result.stderr:
            print(f"  ℹ️  Nema promena za {slug}")
        else:
            push = subprocess.run(
                ["git", "push"],
                cwd=repo_dir, timeout=60, capture_output=True, text=True
            )
            if push.returncode != 0:
                print(f"  ❌ Push greška: {push.stderr[:200]}")
                return ""
            print(f"  ✅ Push završen")
    except subprocess.TimeoutExpired:
        print(f"  ❌ Git timeout")
        return ""
    except Exception as e:
        print(f"  ❌ Git greška: {e}")
        return ""

    site_url = f"{GITHUB_PAGES_BASE}/{slug}/"
    print(f"  ✅ LIVE: {site_url}")
    return site_url


# --- MAIN ---

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Validacija JSON + Jinja2 renderovanje"
    )
    parser.add_argument("json_path", help="Putanja do data JSON fajla")
    parser.add_argument("output_dir", help="Izlazni direktorijum")
    parser.add_argument("--deploy", action="store_true",
                        help="Deploy na GitHub Pages nakon renderovanja")
    parser.add_argument("--playbook", help="Path to niche playbook JSON")
    args = parser.parse_args()

    json_path = args.json_path
    output_dir = args.output_dir
    template_dir = os.path.dirname(os.path.abspath(__file__))

    # Učitaj JSON
    print(f"\n📂 Učitavam: {json_path}")
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"  ❌ Fajl ne postoji: {json_path}")
        sys.exit(1)
    except PermissionError:
        print(f"  ❌ Nema dozvolu za čitanje: {json_path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"  ❌ Neispravan JSON: {e}")
        sys.exit(1)

    # Ukloni meta polja (_komentar, _REQUIRED, _OPTIONAL, _generated, itd.)
    data = {k: v for k, v in data.items() if not k.startswith("_")}

    # Load playbook if provided
    playbook = None
    if args.playbook:
        try:
            with open(args.playbook, "r", encoding="utf-8") as f:
                playbook = json.load(f)
            print(f"  Playbook: {args.playbook}")
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"  Playbook error: {e}")

    # Enrich pre validacije (popunjava vlasnika, probleme, temu, vrednosti)
    json_dir = os.path.dirname(os.path.abspath(json_path))
    data = enrich_schema(data, json_dir=json_dir, playbook=playbook)

    # Validacija
    print(f"\n🔍 Validacija ({len(data)} polja)...")
    errors = validate(data)

    hard_errors = [e for e in errors if e.level == "ERROR"]
    warnings = [e for e in errors if e.level == "WARN"]

    if warnings:
        print(f"\n⚠️  Upozorenja ({len(warnings)}):")
        for w in warnings:
            print(w)

    if hard_errors:
        print(f"\n❌ Greške ({len(hard_errors)}) - POPRAVI PRE RENDEROVANJA:")
        for e in hard_errors:
            print(e)
        print(f"\n💡 Popravi greške u {json_path} i pokreni ponovo.")
        sys.exit(1)

    # Renderovanje
    print(f"\n🔨 Renderujem u {output_dir}...")
    data_dir = os.path.dirname(os.path.abspath(json_path))
    rendered = render_templates(data, template_dir, output_dir, data_dir=data_dir)

    print(f"\n✅ Gotovo! {len(rendered)} fajlova renderovano u {output_dir}/")
    print(f"   Fajlovi: {', '.join(rendered)}")

    # Deploy na GitHub Pages (samo sa --deploy flagom)
    if args.deploy:
        slug = data.get("slug", "")
        if slug and rendered:
            site_url = deploy_to_github(slug, output_dir)
            if site_url:
                print(f"\n🌐 Sajt: {site_url}")
        elif not slug:
            print("\n⚠️  --deploy zahteva 'slug' polje u JSON-u.")


if __name__ == "__main__":
    main()
