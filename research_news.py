#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
research_news.py
Auteur: Francisco Dos Reis (genere avec Devin)
Date: 2026-08-13

Description:
    Agregateur de nouveautes scientifiques pour la mecanique des microstructures
    lattices et l'ingenierie par ordinateur. Interroge trois sources gratuites
    sans authentification :

      - arXiv            : preprints frais (Atom API)
      - Semantic Scholar : preprints + actes de congres, score d'influence
      - OpenAlex         : couverture mondiale large, concepts normalises

    Dedoublonne par titre normalise / DOI / arXiv id, score par theme + recence,
    et produit un digest HTML cliquable ouvert dans le navigateur.

Usage:
    python3 tools/research_news/research_news.py
    python3 tools/research_news/research_news.py --days 7 --no-browser
    python3 tools/research_news/research_news.py --config config.json
    python3 tools/research_news/research_news.py --source arxiv
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from xml.etree import ElementTree as ET

import requests

# ----------------------------------------------------------------------------
# Constantes
# ----------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR  # projet autonome
DEFAULT_CONFIG = SCRIPT_DIR / "config.json"

ARXIV_API = "http://export.arxiv.org/api/query"
S2_API = "https://api.semanticscholar.org/graph/v1/paper/search"
OPENALEX_API = "https://api.openalex.org/works"

USER_AGENT = (
    "research-news/1.0 "
    "(mailto:{mailto})"
)

HTTP_TIMEOUT = 30

# ----------------------------------------------------------------------------
# Utilitaires
# ----------------------------------------------------------------------------


def log(msg: str) -> None:
    print(f"[research_news] {msg}", flush=True)


def load_config(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    # retirer les cles de documentation
    cfg = {k: v for k, v in cfg.items() if not k.startswith("_")}
    if "themes" in cfg:
        cfg["themes"] = {
            k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")}
            for k, v in cfg["themes"].items()
        }
    if "hard_filters" in cfg:
        cfg["hard_filters"] = {
            k: v for k, v in cfg["hard_filters"].items() if not k.startswith("_")
        }
    return cfg


def normalize_title(title: str) -> str:
    """Cle de dedoublonnage : minuscules, sans ponctuation ni espaces multiples."""
    t = re.sub(r"[^\w\s]", " ", (title or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    return t


def parse_date(s: str) -> Optional[dt.datetime]:
    """Parse ISO 8601 / arXiv (YYYY-MM-DDTHH:MM:SSZ ou YYYY-MM-DD)."""
    if not s:
        return None
    s = s.strip().rstrip("Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    # fallback : juste l'annee
    m = re.match(r"(\d{4})", s)
    if m:
        return dt.datetime(int(m.group(1)), 1, 1)
    return None


def days_since(date: Optional[dt.datetime], now: dt.datetime) -> float:
    if date is None:
        return 9999.0
    return (now - date).total_seconds() / 86400.0


# ----------------------------------------------------------------------------
# Modele de papier
# ----------------------------------------------------------------------------


class Paper:
    __slots__ = (
        "title", "abstract", "authors", "date", "venue", "url",
        "pdf_url", "doi", "arxiv_id", "source", "citation_count",
        "influential_count", "themes", "score", "_dedup_key",
    )

    def __init__(self, title: str, abstract: str, authors: List[str],
                 date: Optional[dt.datetime], venue: str, url: str,
                 pdf_url: str, doi: str, arxiv_id: str, source: str,
                 citation_count: int = 0, influential_count: int = 0):
        self.title = title.strip()
        self.abstract = (abstract or "").strip()
        self.authors = authors
        self.date = date
        self.venue = venue
        self.url = url
        self.pdf_url = pdf_url
        self.doi = doi
        self.arxiv_id = arxiv_id
        self.source = source
        self.citation_count = citation_count
        self.influential_count = influential_count
        self.themes: List[str] = []
        self.score: float = 0.0
        self._dedup_key = self._build_dedup_key()

    def _build_dedup_key(self) -> str:
        # priorite : arxiv_id > doi > titre normalise
        if self.arxiv_id:
            return f"arxiv:{self.arxiv_id}"
        if self.doi:
            return f"doi:{self.doi.lower()}"
        return f"title:{normalize_title(self.title)}"

    @property
    def dedup_key(self) -> str:
        return self._dedup_key

    @property
    def text(self) -> str:
        return f"{self.title} {self.abstract}".lower()

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "abstract": self.abstract,
            "authors": self.authors,
            "date": self.date.isoformat() if self.date else None,
            "venue": self.venue,
            "url": self.url,
            "pdf_url": self.pdf_url,
            "doi": self.doi,
            "arxiv_id": self.arxiv_id,
            "source": self.source,
            "citation_count": self.citation_count,
            "influential_count": self.influential_count,
            "themes": self.themes,
            "score": round(self.score, 3),
        }


# ----------------------------------------------------------------------------
# Fetcher arXiv (Atom XML)
# ----------------------------------------------------------------------------

ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


def fetch_arxiv(cfg: dict, since: dt.datetime, now: dt.datetime) -> List[Paper]:
    cats = cfg.get("arxiv_categories", [])
    if not cats:
        return []
    cat_query = " OR ".join(f"cat:{c}" for c in cats)
    max_results = int(cfg.get("max_per_source", 80))
    params = {
        "search_query": cat_query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = f"{ARXIV_API}?{urllib.parse.urlencode(params)}"
    log(f"arXiv : GET {url[:120]}...")
    try:
        resp = requests.get(url, timeout=HTTP_TIMEOUT,
                            headers={"User-Agent": USER_AGENT.format(mailto=cfg.get("mailto", ""))})
        resp.raise_for_status()
    except requests.RequestException as exc:
        log(f"arXiv : erreur HTTP -> {exc}")
        return []

    papers: List[Paper] = []
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        log(f"arXiv : erreur XML -> {exc}")
        return []

    for entry in root.findall(f"{ATOM_NS}entry"):
        title_el = entry.find(f"{ATOM_NS}title")
        summary_el = entry.find(f"{ATOM_NS}summary")
        published_el = entry.find(f"{ATOM_NS}published")
        updated_el = entry.find(f"{ATOM_NS}updated")
        id_el = entry.find(f"{ATOM_NS}id")
        doi_el = entry.find(f"{ARXIV_NS}doi")
        pdf_link = None
        abs_link = None
        for link in entry.findall(f"{ATOM_NS}link"):
            if link.get("title") == "pdf" or link.get("type") == "application/pdf":
                pdf_link = link.get("href")
            elif link.get("type") == "text/html" or link.get("rel") == "alternate":
                abs_link = link.get("href")
        authors = []
        for au in entry.findall(f"{ATOM_NS}author"):
            name = au.find(f"{ATOM_NS}name")
            if name is not None and name.text:
                authors.append(name.text.strip())

        title = (title_el.text or "").strip().replace("\n", " ") if title_el is not None else ""
        abstract = (summary_el.text or "").strip().replace("\n", " ") if summary_el is not None else ""
        pub = parse_date(published_el.text if published_el is not None else "")
        # date de soumission = published ; on filtre sur published
        arxiv_url = (id_el.text or "").strip() if id_el is not None else ""
        arxiv_id = arxiv_url.rsplit("/", 1)[-1] if arxiv_url else ""
        # retirer version (v1, v2...)
        arxiv_id = re.sub(r"v\d+$", "", arxiv_id)
        doi = (doi_el.text or "").strip() if doi_el is not None else ""

        if pub is not None and pub < since:
            continue  # trop ancien

        papers.append(Paper(
            title=title, abstract=abstract, authors=authors,
            date=pub, venue="arXiv",
            url=abs_link or arxiv_url, pdf_url=pdf_link or "",
            doi=doi, arxiv_id=arxiv_id, source="arXiv",
        ))
    log(f"arXiv : {len(papers)} preprints dans la fenetre")
    return papers


# ----------------------------------------------------------------------------
# Fetcher Semantic Scholar
# ----------------------------------------------------------------------------

S2_FIELDS = (
    "title,abstract,authors,year,externalIds,url,citationCount,"
    "influentialCitationCount,publicationDate,venue,openAccessPdf"
)


def _clean_abstract(text: str) -> str:
    """Retire le balisage MathML/XML qui fuit dans les resumes OpenAlex/S2."""
    if not text:
        return ""
    # retirer les balises XML type <mml:...>...</mml:...>
    text = re.sub(r"<[^>]+>", " ", text)
    # normaliser espaces
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_semantic_scholar(cfg: dict, since: dt.datetime, now: dt.datetime) -> List[Paper]:
    if not cfg.get("sources", {}).get("semantic_scholar", True):
        return []
    api_key = cfg.get("semantic_scholar_api_key", "")
    # On construit une requete OR sur les mots-cles les plus discriminants
    # (S2 limite la complexite de la query -> on utilise quelques termes forts).
    queries = [
        "lattice metamaterial mechanical",
        "architected lattice microstructure",
        "topology optimization homogenization",
        "inverse design unit cell",
        "neural operator surrogate topology",
    ]
    year_from = since.year
    year_to = now.year
    max_per_source = int(cfg.get("max_per_source", 80))
    per_query = max(20, max_per_source // len(queries))

    headers = {"User-Agent": USER_AGENT.format(mailto=cfg.get("mailto", ""))}
    if api_key:
        headers["x-api-key"] = api_key
    seen_ids: set = set()
    papers: List[Paper] = []
    consecutive_429 = 0

    for q in queries:
        params = {
            "query": q,
            "fields": S2_FIELDS,
            "limit": per_query,
            "year": f"{year_from}-{year_to}",
        }
        log(f"S2 : search '{q}' (year {year_from}-{year_to})"
            + (" [avec cle API]" if api_key else ""))
        # backoff exponentiel sur 429
        for attempt in range(3):
            try:
                resp = requests.get(S2_API, params=params, headers=headers, timeout=HTTP_TIMEOUT)
            except requests.RequestException as exc:
                log(f"S2 : erreur HTTP -> {exc}")
                resp = None
                break
            if resp.status_code == 429:
                consecutive_429 += 1
                wait = 10 * (2 ** attempt)
                log(f"S2 : rate limit (429), pause {wait}s (tentative {attempt+1}/3)")
                time.sleep(wait)
                continue
            break
        if resp is None or resp.status_code == 429:
            log(f"S2 : abandon de '{q}' apres rate limit")
            continue
        consecutive_429 = 0
        if resp.status_code != 200:
            log(f"S2 : HTTP {resp.status_code} pour '{q}'")
            continue
        try:
            data = resp.json()
        except ValueError:
            continue
        for item in data.get("data", []):
            pid = item.get("paperId")
            if not pid or pid in seen_ids:
                continue
            seen_ids.add(pid)
            title = item.get("title") or ""
            abstract = _clean_abstract(item.get("abstract") or "")
            ext = item.get("externalIds") or {}
            doi = ext.get("DOI", "")
            arxiv_id = ext.get("ArXiv", "")
            pub_date = parse_date(item.get("publicationDate") or "")
            if pub_date is not None and pub_date < since:
                continue
            authors = [a.get("name", "") for a in (item.get("authors") or []) if a.get("name")]
            oa_pdf = (item.get("openAccessPdf") or {}).get("url", "")
            venue = item.get("venue") or ""
            papers.append(Paper(
                title=title, abstract=abstract, authors=authors,
                date=pub_date, venue=venue,
                url=item.get("url", "") or (f"https://doi.org/{doi}" if doi else ""),
                pdf_url=oa_pdf or "",
                doi=doi, arxiv_id=arxiv_id, source="Semantic Scholar",
                citation_count=int(item.get("citationCount") or 0),
                influential_count=int(item.get("influentialCitationCount") or 0),
            ))
        # politesse : sans cle ~100 req/5min ; avec cle ~1 req/s
        time.sleep(3.0 if not api_key else 1.0)
    if consecutive_429 > 0 and not api_key:
        log("S2 : rate limite persistant sans cle API. Ajoutez "
            "'semantic_scholar_api_key' dans config.json (gratuit sur "
            "https://www.semanticscholar.org/product/api) pour des quotas plus larges.")
    log(f"S2 : {len(papers)} papiers dans la fenetre")
    return papers


# ----------------------------------------------------------------------------
# Fetcher OpenAlex
# ----------------------------------------------------------------------------


def fetch_openalex(cfg: dict, since: dt.datetime, now: dt.datetime) -> List[Paper]:
    if not cfg.get("sources", {}).get("openalex", True):
        return []
    max_per_source = int(cfg.get("max_per_source", 80))
    from_date = since.strftime("%Y-%m-%d")
    to_date = now.strftime("%Y-%m-%d")
    headers = {"User-Agent": USER_AGENT.format(mailto=cfg.get("mailto", ""))}

    # Recherche par mots-cles forts (search matche titre/abstract/fulltext)
    queries = [
        "lattice metamaterial",
        "architected lattice microstructure",
        "topology optimization homogenization",
        "inverse design unit cell lattice",
        "neural operator surrogate structural optimization",
    ]
    per_query = max(25, max_per_source // len(queries))
    seen_ids: set = set()
    papers: List[Paper] = []

    for q in queries:
        params = {
            "search": q,
            "filter": f"from_publication_date:{from_date},to_publication_date:{to_date},type:article|preprint",
            "per-page": per_query,
            "sort": "publication_date:desc",
            "mailto": cfg.get("mailto", ""),
        }
        log(f"OpenAlex : search '{q}' ({from_date}..{to_date})")
        try:
            resp = requests.get(OPENALEX_API, params=params, headers=headers, timeout=HTTP_TIMEOUT)
        except requests.RequestException as exc:
            log(f"OpenAlex : erreur HTTP -> {exc}")
            continue
        if resp.status_code != 200:
            log(f"OpenAlex : HTTP {resp.status_code} pour '{q}'")
            continue
        try:
            data = resp.json()
        except ValueError:
            continue
        for item in data.get("results", []):
            oa_id = item.get("id", "")
            if oa_id in seen_ids:
                continue
            seen_ids.add(oa_id)
            title = item.get("title") or item.get("display_name") or ""
            # reconstruct abstract from inverted index
            abstract = _clean_abstract(_openalex_abstract(item.get("abstract_inverted_index")))
            doi_url = item.get("doi") or ""
            doi = doi_url.replace("https://doi.org/", "") if doi_url else ""
            arxiv_id = ""
            for loc in (item.get("locations") or []):
                src = (loc.get("source") or {})
                if "arxiv" in (src.get("display_name") or "").lower():
                    landing = loc.get("landing_page_url") or ""
                    m = re.search(r"arxiv\.org/abs/([\d.]+)", landing)
                    if m:
                        arxiv_id = m.group(1)
                        break
            pub_date = parse_date(item.get("publication_date") or "")
            authors = []
            for a in (item.get("authorships") or []):
                name = (a.get("author") or {}).get("display_name") or a.get("raw_author_name") or ""
                if name:
                    authors.append(name)
            venue = (item.get("primary_location") or {}).get("source", {}) or {}
            venue_name = venue.get("display_name") or ""
            best_oa = item.get("open_access") or {}
            pdf_url = best_oa.get("oa_url") or ""
            url = item.get("id", "") or (f"https://doi.org/{doi}" if doi else "")
            cited = int(item.get("cited_by_count") or 0)
            papers.append(Paper(
                title=title, abstract=abstract, authors=authors,
                date=pub_date, venue=venue_name,
                url=url, pdf_url=pdf_url,
                doi=doi, arxiv_id=arxiv_id, source="OpenAlex",
                citation_count=cited, influential_count=0,
            ))
        time.sleep(0.5)
    log(f"OpenAlex : {len(papers)} papiers dans la fenetre")
    return papers


def _openalex_abstract(inverted: Optional[dict]) -> str:
    if not inverted:
        return ""
    positions: List[tuple] = []
    for word, idxs in inverted.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return " ".join(w for _, w in positions)


# ----------------------------------------------------------------------------
# Scoring + dedoublonnage
# ----------------------------------------------------------------------------


def score_papers(papers: Iterable[Paper], cfg: dict, now: dt.datetime) -> List[Paper]:
    themes = cfg.get("themes", {})
    require_any = cfg.get("hard_filters", {}).get("require_any", [])
    require_any_lower = [t.lower() for t in require_any]

    scored: List[Paper] = []
    for p in papers:
        text = p.text
        # filtre dur : au moins un terme requis
        if require_any_lower and not any(t in text for t in require_any_lower):
            continue
        # themes matches
        matched_themes = []
        theme_score = 0.0
        for theme_name, theme_cfg in themes.items():
            weight = float(theme_cfg.get("weight", 1.0))
            hits = 0
            for kw in theme_cfg.get("keywords", []):
                kwl = kw.lower()
                if kwl in text:
                    hits += 1
                    # bonus si dans le titre
                    if kwl in p.title.lower():
                        hits += 1
            if hits > 0:
                matched_themes.append(theme_name)
                theme_score += weight * min(hits, 5)
        if not matched_themes:
            continue
        # recence : plus recent = meilleur. Decroissance lineaire sur window_days*2.
        window = max(int(cfg.get("window_days", 14)), 1)
        d = days_since(p.date, now)
        recency = max(0.0, 1.0 - d / (window * 2.0))
        # influence (S2/OpenAlex citations) : log-scale, plafonne
        influence = 0.0
        if p.citation_count > 0:
            influence = min(1.5, 0.25 * (1 + min(p.citation_count, 50) / 25.0))
        if p.influential_count > 0:
            influence += 0.5 * min(p.influential_count, 3)
        p.themes = matched_themes
        p.score = theme_score + 0.6 * recency + influence
        scored.append(p)

    # dedoublonnage : garder le papier avec le score max par cle
    by_key: Dict[str, Paper] = {}
    for p in scored:
        cur = by_key.get(p.dedup_key)
        if cur is None or p.score > cur.score:
            # fusion : conserver les sources connues
            if cur is not None:
                p.themes = sorted(set(p.themes + cur.themes))
                p.citation_count = max(p.citation_count, cur.citation_count)
                p.influential_count = max(p.influential_count, cur.influential_count)
                if not p.pdf_url and cur.pdf_url:
                    p.pdf_url = cur.pdf_url
                if not p.doi and cur.doi:
                    p.doi = cur.doi
                if not p.arxiv_id and cur.arxiv_id:
                    p.arxiv_id = cur.arxiv_id
            by_key[p.dedup_key] = p
    return list(by_key.values())


# ----------------------------------------------------------------------------
# Rendu HTML
# ----------------------------------------------------------------------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0f1115">
<meta name="description" content="Digest de nouveautes scientifiques : microstructures lattices, optimisation topologique, ML pour le design.">
<link rel="manifest" href="./manifest.json">
<link rel="icon" type="image/png" href="./icon-192.png">
<link rel="apple-touch-icon" href="./icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="ResearchNews">
<title>Digest recherche - {date}</title>
<style>
:root {{
  --bg:#0f1115; --panel:#171a21; --ink:#e6e8ec; --muted:#9aa4b2;
  --accent:#7cc4ff; --tag:#2b3344; --hot:#ff8a5c; --green:#7ee787;
  --border:#262b36;
}}
* {{ box-sizing:border-box; }}
html {{ -webkit-tap-highlight-color:transparent; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
  font-size:15px; line-height:1.5;
  padding-top:env(safe-area-inset-top); padding-bottom:env(safe-area-inset-bottom); }}
header {{ padding:24px 20px; border-bottom:1px solid var(--border);
  background:linear-gradient(180deg,#1b1f29,#15181f);
  position:sticky; top:0; z-index:10; backdrop-filter:blur(6px); }}
header h1 {{ margin:0 0 6px; font-size:20px; }}
header .meta {{ color:var(--muted); font-size:12px; }}
main {{ max-width:1100px; margin:0 auto; padding:20px 16px 80px; }}
.summary {{ display:flex; gap:10px; flex-wrap:wrap; margin:14px 0 24px; }}
.stat {{ background:var(--panel); border:1px solid var(--border);
  border-radius:10px; padding:10px 14px; min-width:100px; }}
.stat .n {{ font-size:22px; font-weight:700; color:var(--accent); }}
.stat .l {{ color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.06em; }}
section {{ margin:24px 0; }}
section h2 {{ font-size:17px; border-bottom:1px solid var(--border);
  padding-bottom:8px; margin:0 0 12px; }}
section h2 .count {{ color:var(--muted); font-weight:400; font-size:12px; }}
article {{ background:var(--panel); border:1px solid var(--border);
  border-radius:10px; padding:14px 16px; margin:0 0 10px; }}
article:hover {{ border-color:#3a4252; }}
article h3 {{ margin:0 0 6px; font-size:15px; }}
article h3 a {{ color:var(--ink); text-decoration:none; }}
article h3 a:hover {{ color:var(--accent); }}
.row {{ display:flex; gap:8px; flex-wrap:wrap; align-items:center;
  margin:6px 0 8px; font-size:11px; color:var(--muted); }}
.tag {{ background:var(--tag); color:var(--ink); padding:2px 7px;
  border-radius:999px; font-size:10px; }}
.tag.theme {{ background:#1f3a2a; color:var(--green); }}
.tag.src {{ background:#2a2233; color:#c9a6ff; }}
.tag.hot {{ background:#3a2418; color:var(--hot); }}
a.btn {{ display:inline-block; padding:3px 10px; border-radius:6px;
  background:#223047; color:var(--accent); text-decoration:none;
  font-size:11px; border:1px solid #2c3e5a; }}
a.btn.pdf {{ background:#3a2418; color:var(--hot); border-color:#5a3422; }}
.abs {{ color:#c2c8d2; font-size:13px; }}
.authors {{ color:var(--muted); font-size:11px; font-style:italic; }}
footer {{ text-align:center; color:var(--muted); font-size:11px;
  padding:20px; border-top:1px solid var(--border); }}
@media (max-width:640px) {{
  header {{ padding:16px 14px; }}
  header h1 {{ font-size:18px; }}
  main {{ padding:14px 10px 60px; }}
  article {{ padding:12px 12px; }}
}}
</style>
</head>
<body>
<header>
  <h1>Digest nouveautes recherche - {date}</h1>
  <div class="meta">Domaine : microstructures lattices, topologie, ML pour le design
    &middot; Fenetre : {window} jours &middot; Sources : {sources}</div>
</header>
<main>
  <div class="summary">
    <div class="stat"><div class="n">{total}</div><div class="l">Papiers</div></div>
    {stat_blocks}
  </div>
  {sections}
</main>
<footer>Genere par <code>research_news.py</code> &middot;
  {n_sources} sources interrogees &middot; <span id="upd">chargement...</span></footer>
<script>
// Enregistrement du service worker pour le mode hors-ligne / PWA
if ('serviceWorker' in navigator) {{
  navigator.serviceWorker.register('./sw.js').then(() => {{
    document.getElementById('upd').textContent = 'PWA active (hors-ligne dispo)';
  }}).catch((e) => {{
    document.getElementById('upd').textContent = 'PWA: ' + e;
  }});
}} else {{
  document.getElementById('upd').textContent = 'PWA non supportee sur ce navigateur';
}}
</script>
</body>
</html>
"""


def render_html(papers: List[Paper], cfg: dict, now: dt.datetime,
                per_source_counts: Dict[str, int]) -> str:
    by_theme: Dict[str, List[Paper]] = {}
    for p in papers:
        for t in p.themes:
            by_theme.setdefault(t, []).append(p)
    # tri par score desc
    for t in by_theme:
        by_theme[t].sort(key=lambda x: x.score, reverse=True)

    stat_blocks = []
    for src, n in sorted(per_source_counts.items()):
        stat_blocks.append(
            f'<div class="stat"><div class="n">{n}</div><div class="l">{src}</div></div>'
        )
    for theme, plist in sorted(by_theme.items()):
        stat_blocks.append(
            f'<div class="stat"><div class="n">{len(plist)}</div><div class="l">{html.escape(theme)}</div></div>'
        )

    sections = []
    for theme in sorted(by_theme.keys()):
        plist = by_theme[theme]
        cards = []
        for p in plist:
            date_str = p.date.strftime("%Y-%m-%d") if p.date else "?"
            theme_tags = "".join(
                f'<span class="tag theme">{html.escape(t)}</span>' for t in p.themes
            )
            src_tag = f'<span class="tag src">{html.escape(p.source)}</span>'
            hot = ""
            if p.influential_count > 0 or p.citation_count >= 20:
                hot = f'<span class="tag hot">influence {p.citation_count}c</span>'
            links = []
            if p.url:
                links.append(f'<a class="btn" href="{html.escape(p.url)}" target="_blank">Lien</a>')
            if p.pdf_url:
                links.append(f'<a class="btn pdf" href="{html.escape(p.pdf_url)}" target="_blank">PDF</a>')
            elif p.arxiv_id:
                links.append(
                    f'<a class="btn pdf" href="https://arxiv.org/pdf/{html.escape(p.arxiv_id)}" '
                    f'target="_blank">PDF</a>'
                )
            if p.doi:
                links.append(
                    f'<a class="btn" href="https://doi.org/{html.escape(p.doi)}" target="_blank">DOI</a>'
                )
            authors = ", ".join(p.authors[:4])
            if len(p.authors) > 4:
                authors += f" +{len(p.authors)-4}"
            abs_preview = html.escape(p.abstract[:480])
            if len(p.abstract) > 480:
                abs_preview += "..."
            cards.append(f"""
<article>
  <h3><a href="{html.escape(p.url or '#')}" target="_blank">{html.escape(p.title)}</a></h3>
  <div class="row">
    <span>{date_str}</span>
    {src_tag}
    {theme_tags}
    {hot}
    <span>score {p.score:.2f}</span>
  </div>
  <div class="authors">{html.escape(authors)}{(' - ' + html.escape(p.venue)) if p.venue else ''}</div>
  <div class="abs">{abs_preview}</div>
  <div class="row" style="margin-top:10px">{''.join(links)}</div>
</article>""")
        sections.append(
            f'<section><h2>{html.escape(theme)} <span class="count">({len(plist)})</span></h2>'
            + "".join(cards) + "</section>"
        )

    sources_str = ", ".join(sorted(per_source_counts.keys()))
    return HTML_TEMPLATE.format(
        date=now.strftime("%Y-%m-%d %H:%M"),
        window=cfg.get("window_days", 14),
        sources=html.escape(sources_str),
        total=len(papers),
        stat_blocks="".join(stat_blocks),
        sections="".join(sections),
        n_sources=len(per_source_counts),
    )


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Digest de nouveautes scientifiques (lattices/topo/ML).")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help=f"Chemin vers config.json (defaut: {DEFAULT_CONFIG})")
    parser.add_argument("--days", type=int, default=None,
                        help="Fenetre en jours (surcharge config.window_days)")
    parser.add_argument("--no-browser", action="store_true",
                        help="Ne pas ouvrir le navigateur automatiquement")
    parser.add_argument("--source", choices=["arxiv", "semantic_scholar", "openalex"],
                        action="append", default=None,
                        help="Limiter a une ou plusieurs sources (repeter)")
    parser.add_argument("--json", action="store_true",
                        help="Sauver aussi un export JSON des papiers")
    parser.add_argument("--ci", action="store_true",
                        help="Mode CI : genere uniquement index.html (pas de fichier "
                             "horodate), desactive le navigateur, lit S2_API_KEY dans "
                             "l'environnement. Utilise par GitHub Actions.")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    if args.days is not None:
        cfg["window_days"] = args.days
    if args.no_browser or args.ci:
        cfg["open_browser"] = False
    if args.source:
        cfg["sources"] = {k: (k in args.source) for k in
                          ("arxiv", "semantic_scholar", "openalex")}
    # cle API Semantic Scholar depuis l'env (CI) si pas deja dans la config
    if not cfg.get("semantic_scholar_api_key"):
        env_key = os.environ.get("S2_API_KEY", "")
        if env_key:
            cfg["semantic_scholar_api_key"] = env_key
            log("Cle API Semantic Scholar lue depuis S2_API_KEY (env)")

    now = dt.datetime.utcnow()
    since = now - dt.timedelta(days=int(cfg.get("window_days", 14)))
    log(f"Fenetre : {since.date()} -> {now.date()}  ({cfg.get('window_days', 14)} jours)")

    all_papers: List[Paper] = []
    per_source: Dict[str, int] = {}

    if cfg["sources"].get("arxiv", True):
        papers = fetch_arxiv(cfg, since, now)
        per_source["arXiv"] = len(papers)
        all_papers.extend(papers)
    if cfg["sources"].get("semantic_scholar", True):
        papers = fetch_semantic_scholar(cfg, since, now)
        per_source["Semantic Scholar"] = len(papers)
        all_papers.extend(papers)
    if cfg["sources"].get("openalex", True):
        papers = fetch_openalex(cfg, since, now)
        per_source["OpenAlex"] = len(papers)
        all_papers.extend(papers)

    log(f"Total brut : {len(all_papers)} papiers")
    scored = score_papers(all_papers, cfg, now)
    scored.sort(key=lambda x: x.score, reverse=True)
    log(f"Apres filtrage theme + dedoublonnage : {len(scored)} papiers")

    # sortie
    out_dir = PROJECT_ROOT / cfg.get("output_dir", "output")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y-%m-%d_%H%M")

    html_content = render_html(scored, cfg, now, per_source)

    if args.ci:
        # Mode CI : uniquement index.html (GitHub Pages sert la racine de output/)
        index_path = out_dir / "index.html"
        with open(index_path, "w", encoding="utf-8") as fh:
            fh.write(html_content)
        log(f"CI : index.html ecrit -> {index_path}")
        # copier les assets PWA depuis public/ et icons/ si presents
        public_dir = PROJECT_ROOT / "public"
        icons_dir = PROJECT_ROOT / "icons"
        for src in [public_dir / "manifest.json", public_dir / "sw.js",
                    icons_dir / "icon-192.png", icons_dir / "icon-512.png"]:
            if src.exists():
                dst = out_dir / src.name
                dst.write_bytes(src.read_bytes())
                log(f"CI : copie {src.name} -> {dst}")
        print(f"\n[CI] Digest genere : {index_path}")
        print(f"[CI] Stats         : {per_source} -> {len(scored)} papiers retenus")
        return 0

    # mode local : fichier horodate + index.html
    html_path = out_dir / f"digest_{stamp}.html"
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html_content)
    log(f"HTML : {html_path}")

    if args.json:
        json_path = out_dir / f"digest_{stamp}.json"
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump([p.to_dict() for p in scored], fh, indent=2, ensure_ascii=False)
        log(f"JSON : {json_path}")

    # index.html = dernier digest
    index_path = out_dir / "index.html"
    with open(index_path, "w", encoding="utf-8") as fh:
        fh.write(html_content)

    print(f"\nDigest genere : {html_path}")
    print(f"Index live    : {index_path}")
    print(f"Stats         : {per_source} -> {len(scored)} papiers retenus")

    if cfg.get("open_browser", True):
        try:
            webbrowser.open(f"file://{html_path}")
            log("Navigateur ouvert")
        except Exception as exc:
            log(f"Ouverture navigateur impossible : {exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
