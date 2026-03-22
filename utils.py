"""
utils.py - Shared utilities (Cyrillic→Latin, HTML escaping)
Used by both render.py and scraper.py
"""

CYR_TO_LAT = {
    'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D', 'Ђ': 'Đ', 'Е': 'E',
    'Ж': 'Ž', 'З': 'Z', 'И': 'I', 'Ј': 'J', 'К': 'K', 'Л': 'L', 'Љ': 'Lj',
    'М': 'M', 'Н': 'N', 'Њ': 'Nj', 'О': 'O', 'П': 'P', 'Р': 'R', 'С': 'S',
    'Т': 'T', 'Ћ': 'Ć', 'У': 'U', 'Ф': 'F', 'Х': 'H', 'Ц': 'C', 'Ч': 'Č',
    'Џ': 'Dž', 'Ш': 'Š',
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'ђ': 'đ', 'е': 'e',
    'ж': 'ž', 'з': 'z', 'и': 'i', 'ј': 'j', 'к': 'k', 'л': 'l', 'љ': 'lj',
    'м': 'm', 'н': 'n', 'њ': 'nj', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's',
    'т': 't', 'ћ': 'ć', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'c', 'ч': 'č',
    'џ': 'dž', 'ш': 'š',
}


def cyr_to_lat(text: str) -> str:
    """Transliteruje ćirilicu u latinicu."""
    if not isinstance(text, str):
        return text
    return ''.join(CYR_TO_LAT.get(ch, ch) for ch in text)


def has_cyrillic(text: str) -> bool:
    """Proverava da li string sadrži ćirilična slova."""
    if not isinstance(text, str):
        return False
    return any('\u0400' <= ch <= '\u04FF' for ch in text)


def strip_diacritics(s: str) -> str:
    """Remove Serbian diacritics for fuzzy keyword matching."""
    return (s.replace('\u0161', 's').replace('\u010d', 'c').replace('\u0107', 'c')
             .replace('\u017e', 'z').replace('\u0111', 'd')
             .replace('\u0160', 'S').replace('\u010c', 'C').replace('\u0106', 'C')
             .replace('\u017d', 'Z').replace('\u0110', 'D'))
