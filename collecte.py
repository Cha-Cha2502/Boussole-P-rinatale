#!/usr/bin/env python3
"""Collecte automatique des articles de la Boussole Périnatale.

1. Lit les flux RSS et les recherches Google Actualités listés dans sources.json.
2. Garde les articles liés à la périnatalité (mots-clés).
3. Ouvre chaque nouvel article pour récupérer son vrai lien et sa description.
4. Si une clé Gemini est fournie (secret GITHUB « GEMINI_API_KEY »), Gemini :
   écarte les articles hors sujet, corrige la catégorie, rédige un résumé neutre
   et propose des points de discussion. Sans clé, la description du site sert de résumé.
"""
import hashlib
import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import feedparser

try:
    from googlenewsdecoder import gnewsdecoder
except Exception:  # module absent : on garde les liens Google
    gnewsdecoder = None

# Fonctionne avec les fichiers rangés en dossiers (scripts/, site/) ou tous à la racine du dépôt
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent if HERE.name == "scripts" else HERE
SOURCES = ROOT / "sources.json"
CURATED = ROOT / "curated.json"
OUT = (ROOT / "site" / "articles.json") if (ROOT / "site").is_dir() else (ROOT / "articles.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
NOW = datetime.now(timezone.utc)

IA_DEFAUT = {
    # Modèles essayés dans l'ordre (les alias « latest » suivent les nouvelles versions de Google)
    "modeles": ["gemini-flash-lite-latest", "gemini-3.1-flash-lite", "gemini-flash-latest"],
    "articles_par_appel": 5,
    "appels_max_par_collecte": 25,
    "pause_secondes": 7,
}
MAX_PAGES_PAR_COLLECTE = 40
# Garde-fous de durée : la collecte s'arrête proprement et enregistre son travail avant la limite de GitHub
START = time.time()
BUDGET_PAGES = 8 * 60      # lecture des pages : 8 minutes maximum
BUDGET_TOTAL = 18 * 60     # collecte complète : 18 minutes maximum


def elapsed():
    return time.time() - START


def say(msg):
    print(f"[{int(elapsed()) // 60:02d}:{int(elapsed()) % 60:02d}] {msg}", flush=True)

PROMPT = """Tu aides une accompagnante périnatale (doula) : une professionnelle non médicale qui soutient les parents sur le plan émotionnel, informationnel et pratique avant, pendant et après la naissance. Elle ne pose pas de diagnostic et n'intervient pas sur le plan médical.

Voici des articles de presse ou institutionnels. Pour CHACUN, fournis :
- "id" : l'identifiant donné, recopié à l'identique.
- "pertinent" : true si l'article apporte une information utile sur la grossesse, l'accouchement, le post-partum, les nouveau-nés, la parentalité des premiers mois, la santé périnatale ou les droits et l'organisation des soins qui s'y rattachent ; false pour un sujet sans lien, un simple fait divers ou de la presse people sans information utile.
- "categorie" : "sci" (études, recherche, recommandations médicales et de santé publique), "pol" (lois, réformes, décisions publiques, organisation des soins, droits et congés) ou "soc" (témoignages, société, médias, vécu des parents).
- "resume" : 2 à 3 phrases en français, neutres et factuelles, écrites avec tes propres mots (ne recopie aucune phrase de l'article). Appuie-toi uniquement sur le texte fourni, n'invente aucun chiffre ni aucun fait. Si le texte fourni est trop court, résume seulement ce qui est certain.
- "points" : 2 ou 3 pistes de réflexion concrètes qu'elle peut en tirer pour sa pratique : comment en parler avec les parents qu'elle accompagne, comment adapter son écoute ou ses conseils non médicaux, quelles limites garder vis-à-vis du champ médical. Une phrase par point, formulation accessible, sans numérotation.
{consigne_speciale}
Réponds UNIQUEMENT avec un tableau JSON de la forme :
[{{"id": "...", "pertinent": true, "categorie": "sci", "resume": "...", "points": ["...", "..."]}}]

Articles :
{articles}"""


# ---------------------------------------------------------------- utilitaires
def norm(s):
    s = unicodedata.normalize("NFD", s or "").lower()
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip()


def clean_text(raw, limit=320):
    txt = html.unescape(re.sub(r"<[^>]+>", " ", raw or ""))
    txt = re.sub(r"\s+", " ", txt).strip()
    if limit and len(txt) > limit:
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


def fetch_feed(url):
    feed = feedparser.parse(url, agent=UA)
    if feed.get("bozo") and not feed.entries:
        raise RuntimeError(str(feed.get("bozo_exception", "flux illisible")))
    return feed.entries


def make_id(key):
    return "a-" + hashlib.sha1(norm(key).encode()).hexdigest()[:12]


# ---------------------------------------------------------------- lecture des articles
def resolve_google(url):
    if "news.google.com" not in url or gnewsdecoder is None:
        return url
    try:
        r = gnewsdecoder(url, interval=1)
        if r and (r.get("status") or r.get("success")) and r.get("decoded_url"):
            return r["decoded_url"]
    except Exception:
        pass
    return url


def meta(page, name):
    pats = [
        r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]*content=["\']([^"\']*)' % re.escape(name),
        r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']%s["\']' % re.escape(name),
    ]
    for p in pats:
        m = re.search(p, page, re.I)
        if m and m.group(1).strip():
            return clean_text(m.group(1), 0)
    return ""


def read_page(url):
    """Renvoie (description, extrait du texte) ; chaînes vides si la page est inaccessible."""
    if "news.google.com" in url:
        return "", ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9"})
        with urllib.request.urlopen(req, timeout=10) as r:
            if "html" not in (r.headers.get("Content-Type") or ""):
                return "", ""
            page = r.read(1_500_000).decode(r.headers.get_content_charset() or "utf-8", "ignore")
    except Exception:
        return "", ""
    desc = meta(page, "og:description") or meta(page, "description") or meta(page, "twitter:description")
    body = re.sub(r"(?is)<(script|style|nav|header|footer|aside|form)[^>]*>.*?</\1>", " ", page)
    paras = [clean_text(p, 0) for p in re.findall(r"(?is)<p[^>]*>(.*?)</p>", body)]
    paras = [p for p in paras if len(p) > 70 and "cookie" not in p.lower()]
    excerpt = ""
    for p in paras:
        if len(excerpt) + len(p) > 2200:
            break
        excerpt += p + "\n"
    return desc, excerpt.strip()


# ---------------------------------------------------------------- Gemini
class GeminiStop(Exception):
    pass


def gemini_call(key, models, prompt):
    """Appelle Gemini et renvoie la réponse JSON décodée (ou None). `models` est modifiée si un modèle a disparu."""
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }).encode()
    while models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{models[0]}:generateContent"
        for attempt in range(2):
            req = urllib.request.Request(url, data=body, method="POST", headers={
                "Content-Type": "application/json", "x-goog-api-key": key})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = json.loads(r.read().decode())
                parts = data["candidates"][0]["content"]["parts"]
                text = "".join(p.get("text", "") for p in parts).strip()
                text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
                return json.loads(text)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "ignore")[:400]
                if e.code == 429:
                    if attempt == 0:
                        time.sleep(60)
                        continue
                    raise GeminiStop("quota gratuit atteint, reprise à la prochaine collecte")
                if e.code in (401, 403) or (e.code == 400 and "API" in detail and "key" in detail.lower()):
                    raise GeminiStop("clé Gemini refusée — vérifiez le secret GEMINI_API_KEY")
                if e.code == 404:
                    models.pop(0)  # modèle retiré par Google : on essaie le suivant
                    break
                return None
            except Exception:
                return None
    raise GeminiStop("aucun modèle Gemini disponible — mettez à jour la liste « modeles » dans sources.json")


def enrich_with_ai(items, key, cfg_ia, log):
    """items : liste de dicts article (modifiés sur place). Renvoie le nombre d'articles traités."""
    models = list(cfg_ia["modeles"])
    size = cfg_ia["articles_par_appel"]
    done = calls = 0
    for i in range(0, len(items), size):
        if elapsed() > BUDGET_TOTAL:
            log.append("IA : temps de collecte écoulé, la suite sera traitée à la prochaine collecte")
            break
        if calls >= cfg_ia["appels_max_par_collecte"]:
            log.append("IA : limite d'appels atteinte, la suite sera traitée à la prochaine collecte")
            break
        batch = items[i:i + size]
        blocks, special = [], ""
        for a in batch:
            txt = a.get("_excerpt") or a.get("summary") or ""
            blocks.append(f"--- id: {a['id']}\nTitre : {a['title']}\nSource : {a['source']}\n"
                          f"Description : {a.get('_desc', '')}\nTexte : {txt[:2200]}")
        if any(a.get("curated") for a in batch):
            special = "\nPour les articles marqués « vérifié », garde pertinent=true et la catégorie donnée.\n"
            blocks = [b + ("\n(vérifié)" if a.get("curated") else "") for b, a in zip(blocks, batch)]
        prompt = PROMPT.format(consigne_speciale=special, articles="\n\n".join(blocks))
        if calls:
            time.sleep(cfg_ia["pause_secondes"])
        calls += 1
        say(f"Gemini : lot {calls} ({len(batch)} articles)")
        try:
            res = gemini_call(key, models, prompt)
        except GeminiStop as e:
            log.append("IA : " + str(e))
            break
        if not isinstance(res, list):
            continue
        by_id = {str(r.get("id")): r for r in res if isinstance(r, dict)}
        for a in batch:
            r = by_id.get(a["id"])
            if not r:
                continue
            pts = [clean_text(p, 400) for p in (r.get("points") or []) if isinstance(p, str) and p.strip()][:3]
            if pts:
                a["points"] = pts
            if a.get("curated"):
                a["enriched"] = True
                done += 1
                continue
            if r.get("pertinent") is False:
                a["horsSujet"] = True
            if r.get("categorie") in ("sci", "pol", "soc"):
                a["category"] = r["categorie"]
            if isinstance(r.get("resume"), str) and len(r["resume"].strip()) > 40:
                a["summary"] = clean_text(r["resume"], 700)
                a["summaryAI"] = True
            a["enriched"] = True
            done += 1
    return done


# ---------------------------------------------------------------- programme principal
def main():
    cfg = json.loads(SOURCES.read_text(encoding="utf-8"))
    rg = cfg["reglages"]
    cfg_ia = {**IA_DEFAUT, **cfg.get("ia", {})}
    match = build_matcher(cfg["mots_cles"])
    exclude = build_matcher(cfg["exclusions"]) if cfg.get("exclusions") else None
    official = cfg.get("domaines_officiels", [])
    cutoff_new = NOW - timedelta(days=rg["jours_max_nouveaux"])
    cutoff_keep = (NOW - timedelta(days=rg["jours_conservation"])).date().isoformat()
    key = os.environ.get("GEMINI_API_KEY", "").strip()

    previous, ignored = {}, {}
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
            previous = {a["id"]: a for a in old.get("articles", [])}
            ignored = old.get("ignores", {})
        except Exception:
            pass

    found, errors, source_names, log = [], [], [], []

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
            "id": make_id(title), "category": category,
            "priority": bool(is_off and pub >= NOW - timedelta(days=rg["jours_prioritaire"])),
            "title": title.strip(), "source": src_name, "date": fmt_fr(pub),
            "sortDate": pub.date().isoformat(), "url": url, "summary": summary, "auto": True,
        })

    say("Lecture des flux")
    for f in cfg["flux"]:
        source_names.append(f["nom"])
        try:
            for e in fetch_feed(f["url"]):
                summ = clean_text(e.get("summary") or e.get("description") or "")
                add(clean_text(e.get("title"), 300), e.get("link"), summ, f["nom"], f["category"],
                    entry_date(e), f.get("officiel") or is_official(e.get("link", ""), official), f.get("filtre", True))
        except Exception as ex:
            errors.append(f"{f['nom']} : {ex}")

    for g in cfg.get("google_news", []):
        q = quote(g["requete"] + " when:14d")
        url = f"https://news.google.com/rss/search?q={q}&hl=fr&gl=FR&ceid=FR:fr"
        try:
            for e in fetch_feed(url):
                src = e.get("source") or {}
                src_name = src.get("title") or "Google Actualités"
                title = clean_text(e.get("title"), 300)
                if title.endswith(" - " + src_name):
                    title = title[: -len(" - " + src_name)]
                add(title, e.get("link"), "", src_name, g["category"], entry_date(e),
                    is_official(src.get("href", ""), official), True)
        except Exception as ex:
            errors.append(f"Google Actualités « {g['requete']} » : {ex}")
    if cfg.get("google_news"):
        source_names.append("Google Actualités (recherches par mots-clés)")

    # Fusion : un article déjà traité est conservé tel quel ; les articles jugés hors sujet ne reviennent pas
    merged = {}
    for a in list(previous.values()) + found:
        if not a.get("auto") or a["id"] in ignored:
            continue
        old = merged.get(a["id"]) or previous.get(a["id"])
        if old and old is not a:
            if old.get("enriched") or old.get("pageRead"):
                merged[a["id"]] = old
                continue
            a["addedAt"] = old.get("addedAt", a.get("addedAt"))
            if old.get("summary") and not a.get("summary"):
                a["summary"] = old["summary"]
        a.setdefault("addedAt", NOW.isoformat(timespec="seconds"))
        merged[a["id"]] = a

    kept = [a for a in merged.values() if a["sortDate"] >= cutoff_keep]
    kept.sort(key=lambda a: a["sortDate"], reverse=True)

    # Lecture des pages (vrai lien + description) pour les nouveaux articles
    todo = [a for a in kept if not a.get("pageRead") or (key and not a.get("enriched"))][:MAX_PAGES_PAR_COLLECTE]
    say(f"{len(found)} articles trouvés dans les flux, {len(todo)} page(s) à lire")
    read = 0
    for a in todo:
        if elapsed() > BUDGET_PAGES:
            log.append("Lecture des pages interrompue (temps écoulé), reprise à la prochaine collecte")
            break
        read += 1
        if read % 10 == 0:
            say(f"{read} pages lues")
        a["url"] = resolve_google(a["url"])
        desc, excerpt = read_page(a["url"])
        a["_desc"], a["_excerpt"] = desc, excerpt
        if desc and not a.get("summary"):
            a["summary"] = clean_text(desc, 400)
        a["pageRead"] = True
    if read:
        log.append(f"{read} page(s) d'articles lue(s)")

    curated = json.loads(CURATED.read_text(encoding="utf-8")) if CURATED.exists() else []
    for c in curated:
        c["curated"] = True
        p = previous.get(c["id"])
        if p and p.get("points"):
            c["points"], c["enriched"] = p["points"], True

    # Enrichissement par Gemini
    if key:
        pending = [a for a in kept if a.get("pageRead") and not a.get("enriched")] + \
                  [c for c in curated if not c.get("enriched")]
        n = enrich_with_ai(pending, key, cfg_ia, log)
        log.append(f"IA : {n} article(s) résumé(s) et commenté(s)")
    else:
        log.append("IA non configurée : ajoutez le secret GEMINI_API_KEY pour les résumés et points de discussion")

    for a in kept:
        a.pop("_desc", None)
        a.pop("_excerpt", None)
        if a.get("horsSujet"):
            ignored[a["id"]] = a["sortDate"]
    ignored = {k: v for k, v in ignored.items() if v >= cutoff_keep}
    visible = [a for a in kept if not a.get("horsSujet")]

    final = []
    for cat in ("sci", "pol", "soc"):
        final += [a for a in visible if a["category"] == cat][: rg["max_par_categorie"]]
    auto_ids = {a["id"] for a in final}
    final = [c for c in curated if c["id"] not in auto_ids] + final

    added = sum(1 for a in final if a.get("auto") and a["id"] not in previous)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "updatedAt": NOW.isoformat(timespec="seconds"),
        "sources": source_names,
        "errors": errors,
        "journal": log,
        "ignores": ignored,
        "articles": final,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"{len(final)} articles ({added} nouveaux), {len(errors)} source(s) en erreur, {len(ignored)} écartés")
    for line in errors + log:
        print("  · " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
