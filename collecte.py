#!/usr/bin/env python3
"""Collecte automatique des articles de la Boussole Périnatale.

Lit les flux RSS listés dans sources.json, garde les articles liés à la
périnatalité, et écrit site/articles.json. Aucune IA : le résumé affiché
est le chapô fourni par la source elle-même.
"""
import hashlib
import html
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import feedparser

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "sources.json"
CURATED = ROOT / "curated.json"
OUT = ROOT / "site" / "articles.json"
UA = "Mozilla/5.0 (compatible; BoussolePerinatale/1.0; +https://github.com)"
NOW = datetime.now(timezone.utc)


def norm(s):
    s = unicodedata.normalize("NFD", s or "").lower()
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip()


def clean_text(raw, limit=320):
    txt = html.unescape(re.sub(r"<[^>]+>", " ", raw or ""))
    txt = re.sub(r"\s+", " ", txt).strip()
    if len(txt) > limit:
        txt = txt[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return txt


def entry_date(e):
    for k in ("published_parsed", "updated_parsed"):
        t = e.get(k)
        if t:
            return datetime.fromtimestamp(time.mktime(t), tz=timezone.utc)
    return None


def fmt_fr(d):
    mois = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
            "août", "septembre", "octobre", "novembre", "décembre"]
    return f"{d.day} {mois[d.month - 1]} {d.year}"


def domain(url):
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def is_official(url, official_domains):
    d = domain(url)
    return any(d == o or d.endswith("." + o) for o in official_domains)


def build_matcher(keywords):
    parts = [re.escape(norm(k)) for k in keywords]
    return re.compile(r"(?<![\w-])(" + "|".join(parts) + r")(?![\w-])")


def fetch(url):
    feed = feedparser.parse(url, agent=UA)
    if feed.get("bozo") and not feed.entries:
        raise RuntimeError(str(feed.get("bozo_exception", "flux illisible")))
    return feed.entries


def make_id(key):
    return "a-" + hashlib.sha1(norm(key).encode()).hexdigest()[:12]


def main():
    cfg = json.loads(SOURCES.read_text(encoding="utf-8"))
    rg = cfg["reglages"]
    match = build_matcher(cfg["mots_cles"])
    exclude = build_matcher(cfg["exclusions"]) if cfg.get("exclusions") else None
    official = cfg.get("domaines_officiels", [])
    cutoff_new = NOW - timedelta(days=rg["jours_max_nouveaux"])
    cutoff_keep = NOW - timedelta(days=rg["jours_conservation"])

    previous = {}
    if OUT.exists():
        try:
            for a in json.loads(OUT.read_text(encoding="utf-8")).get("articles", []):
                previous[a["id"]] = a
        except Exception:
            pass

    found, errors, source_names = [], [], []

    def add(title, url, summary, src_name, category, pub, is_off, check_kw):
        if not title or not url or not pub or pub < cutoff_new:
            return
        t, sm = norm(title), norm(summary)
        if exclude and exclude.search(t):
            return
        # Pertinence : un mot-clé dans le titre, ou au moins deux mots-clés différents dans le chapô
        if check_kw and not match.search(t) and len(set(match.findall(sm))) < 2:
            return
        found.append({
            "id": make_id(title),
            "category": category,
            "priority": bool(is_off and pub >= NOW - timedelta(days=rg["jours_prioritaire"])),
            "title": title.strip(),
            "source": src_name,
            "date": fmt_fr(pub),
            "sortDate": pub.date().isoformat(),
            "url": url,
            "summary": summary,
            "auto": True,
        })

    for f in cfg["flux"]:
        source_names.append(f["nom"])
        try:
            for e in fetch(f["url"]):
                summ = clean_text(e.get("summary") or e.get("description") or "")
                add(clean_text(e.get("title"), 300), e.get("link"), summ, f["nom"], f["category"],
                    entry_date(e), f.get("officiel") or is_official(e.get("link", ""), official), f.get("filtre", True))
        except Exception as ex:
            errors.append(f"{f['nom']} : {ex}")

    for g in cfg.get("google_news", []):
        q = quote(g["requete"] + " when:14d")
        url = f"https://news.google.com/rss/search?q={q}&hl=fr&gl=FR&ceid=FR:fr"
        try:
            for e in fetch(url):
                src = e.get("source") or {}
                src_name = src.get("title") or "Google Actualités"
                title = clean_text(e.get("title"), 300)
                suffix = " - " + src_name
                if title.endswith(suffix):
                    title = title[: -len(suffix)]
                add(title, e.get("link"), "", src_name, g["category"], entry_date(e),
                    is_official(src.get("href", ""), official), True)
        except Exception as ex:
            errors.append(f"Google Actualités « {g['requete']} » : {ex}")
    if cfg.get("google_news"):
        source_names.append("Google Actualités (recherches par mots-clés)")

    # Fusion : un article déjà connu garde sa date d'ajout ; un doublon (même titre) n'est gardé qu'une fois
    merged = {}
    for a in list(previous.values()) + found:
        if not a.get("auto"):
            continue
        old = merged.get(a["id"]) or previous.get(a["id"])
        if old and old is not a:
            a["addedAt"] = old.get("addedAt", a.get("addedAt"))
            if old.get("summary") and not a.get("summary"):
                a["summary"] = old["summary"]
        a.setdefault("addedAt", NOW.isoformat(timespec="seconds"))
        merged[a["id"]] = a

    kept = [a for a in merged.values() if a["sortDate"] >= cutoff_keep.date().isoformat()]
    final = []
    for cat in ("sci", "pol", "soc"):
        items = sorted((a for a in kept if a["category"] == cat), key=lambda a: a["sortDate"], reverse=True)
        final += items[: rg["max_par_categorie"]]

    curated = json.loads(CURATED.read_text(encoding="utf-8")) if CURATED.exists() else []
    for c in curated:
        c["curated"] = True
    auto_ids = {a["id"] for a in final}
    final = [c for c in curated if c["id"] not in auto_ids] + final

    added = sum(1 for a in final if a.get("auto") and a["id"] not in previous)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "updatedAt": NOW.isoformat(timespec="seconds"),
        "sources": source_names,
        "errors": errors,
        "articles": final,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"{len(final)} articles ({added} nouveaux), {len(errors)} source(s) en erreur")
    for err in errors:
        print("  ! " + err)
    return 0


if __name__ == "__main__":
    sys.exit(main())
