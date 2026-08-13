# research_news — Digest de nouveautés scientifiques

Outil Python qui agrège les **prépublications et articles récents** dans votre domaine
(microstructures lattices, optimisation topologique, ML pour le design) depuis trois
sources **gratuites et sans abonnement**, puis produit un **digest HTML cliquable**.

## Sources

| Source | Couverture | Accès |
|---|---|---|
| **arXiv** | Préprints frais (quotidien) | API Atom, sans auth |
| **Semantic Scholar** | Préprints + actes de congrès, score d'influence | REST, sans auth (limité) ou clé API gratuite |
| **OpenAlex** | Couverture mondiale large, concepts normalisés | REST, sans auth |

## Prérequis

- Python 3.8+
- `requests` (déjà installé dans l'environnement du projet)

Aucune autre dépendance. Pas de `feedparser` ni `arxiv` externes — l'analyse Atom
se fait avec la stdlib (`xml.etree`).

## Usage

```bash
# Digest complet (3 sources, fenetre 14 jours, ouverture navigateur)
python3 tools/research_news/research_news.py

# Fenetre plus courte, sans ouvrir le navigateur
python3 tools/research_news/research_news.py --days 7 --no-browser

# Une seule source
python3 tools/research_news/research_news.py --source arxiv
python3 tools/research_news/research_news.py --source openalex --source arxiv

# Export JSON en plus du HTML
python3 tools/research_news/research_news.py --json
```

## Sortie

Les fichiers sont écrits dans `DATAS/_research_news/` (ignoré par git via `DATAS/`) :

- `digest_YYYY-MM-DD_HHMM.html` — digest horodaté
- `digest_YYYY-MM-DD_HHMM.json` — export structuré (avec `--json`)
- `index.html` — toujours le dernier digest (pratique pour un signet)

## Configuration

Tout est éditable dans `tools/research_news/config.json` :

- `window_days` — fenêtre temporelle en jours
- `max_per_source` — nombre max de résultats par source
- `arxiv_categories` — catégories arXiv interrogées (`cond-mat.mtrl-sci`,
  `physics.comp-ph`, `cs.CE`, `cs.CG`, `cs.LG`, `math.OC`, `cs.AI`)
- `themes` — groupes de mots-clés avec pondération. Un papier doit matcher
  au moins un thème pour être retenu.
- `hard_filters.require_any` — liste de termes dont **au moins un** doit
  apparaître (titre OU résumé) pour qu'un papier soit conservé. Filtre
  anti-bruit. Laisser vide pour tout garder.
- `semantic_scholar_api_key` — clé API gratuite (optionnelle) pour lever les
  rate-limits de Semantic Scholar. S'obtient sur
  <https://www.semanticscholar.org/product/api> (inscription en 1 min).

## Scoring

Chaque papier retenu reçoit un score = `thème + récence + influence` :

- **Thème** : somme pondérée des mots-clés matchés (bonus si dans le titre),
  plafonné à 5 hits par thème.
- **Récence** : décroissance linéaire sur `2 × window_days`.
- **Influence** : `0.25 × log(citations)` + bonus si `influentialCitationCount > 0`
  (Semantic Scholar uniquement).

## Dédoublonnage

Par clé prioritaire : `arxiv_id` > `DOI` > titre normalisé (minuscules, sans
ponctuation). En cas de doublon, on garde la meilleure entrée et on fusionne
les métadonnées (sources, PDF, citations, thèmes).

## Notes

- **Semantic Scholar sans clé API** est sévèrement rate-limité (429 fréquents).
  En pratique arXiv + OpenAlex couvrent déjà très bien le domaine ; S2 devient
  utile avec une clé gratuite.
- **arXiv** ne retourne que les N préprints les plus récents toutes catégories
  confondues : pour un domaine niche, la plupart ne matchent pas — c'est
  normal, le filtre thème fait le tri.
- **OpenAlex** est la source la plus large et la plus fiable ici (couvre
  revues + actes de congrès + préprints, avec DOI et PDF open access quand
  disponible).
- L'User-Agent inclut un `mailto` (politesse OpenAlex). Mettez votre vrai
  email dans `config.json` pour le "polite pool" d'OpenAlex (quotas plus
  larges).

## Automatisation (optionnel)

Pour un digest hebdomadaire automatique, ajoutez par exemple une tâche
planifiée Windows ou un cron :

```bash
# cron Linux/WSL : chaque lundi 08:00
0 8 * * 1 cd /mnt/c/Users/franc/source/repos/LAGAI-V8 && \
  python3 tools/research_news/research_news.py --days 7 >> \
  DATAS/_research_news/cron.log 2>&1
```

```powershell
# Planificateur de tâches Windows : chaque lundi 08:00
schtasks /create /tn "ResearchNews" /sc weekly /d MON /st 08:00 /tr \
  "python C:\Users\franc\source\repos\LAGAI-V8\tools\research_news\research_news.py --days 7"
```
