# research_news — Digest de nouveautés scientifiques (PWA)

Agrégateur Python qui collecte les prépublications et articles récents en
microstructures lattices, optimisation topologique et ML pour le design,
depuis trois sources gratuites (arXiv, Semantic Scholar, OpenAlex), et
produit un **digest HTML** installable comme **PWA** sur Android (icône sur
l'écran d'accueil, mise à jour automatique via GitHub Actions).

## Architecture (option C : CI + PWA)

```
GitHub Actions (chaque nuit, 03:00 UTC)
  └─ python3 research_news.py --ci
     └─ output/index.html + assets PWA
        └─ push sur branche gh-pages
           └─ GitHub Pages sert https://<user>.github.io/research_news/

Android : Chrome > ouvrir l'URL > "Ajouter à l'écran d'accueil"
          → icône plein écran, digest auto-mis-à-jour chaque nuit
```

## Sources

| Source | Couverture | Accès |
|---|---|---|
| **arXiv** | Préprints frais (quotidien) | API Atom, sans auth |
| **Semantic Scholar** | Préprints + actes de congrès, score d'influence | REST, sans auth (limité) ou clé API gratuite |
| **OpenAlex** | Couverture mondiale large, concepts normalisés | REST, sans auth |

## Prérequis

- Python 3.8+ avec `requests`
- Un compte GitHub (gratuit) pour la publication auto

## Usage local

```bash
# Digest complet (3 sources, fenetre 14 jours, ouverture navigateur)
python3 research_news.py

# Fenetre plus courte, sans ouvrir le navigateur
python3 research_news.py --days 7 --no-browser

# Mode CI (genere uniquement output/index.html + copie les assets PWA)
python3 research_news.py --ci

# Une seule source
python3 research_news.py --source arxiv --source openalex

# Export JSON en plus du HTML (mode local seulement)
python3 research_news.py --json
```

## Déploiement GitHub Pages (une seule fois)

1. **Créer le repo sur GitHub** et pousser le code :
   ```bash
   cd ~/research_news
   git remote add origin git@github.com:<votre-user>/research_news.git
   git push -u origin main
   ```
   Le repo peut être **public** (GitHub Pages gratuit) ou privé
   (Pages nécessite GitHub Pro, 4$/mois).

2. **Activer GitHub Pages** : Settings > Pages > Source = "Deploy from a
   branch" > Branch = `gh-pages` > Folder = `/ (root)`.
   Le workflow crée la branche `gh-pages` automatiquement au premier run.

3. **Lancer le premier run** : onglet Actions > "Digest nocturne" >
   "Run workflow" > vert. Au bout de ~1 min, la branche `gh-pages` est
   créée et le site est en ligne.

4. **Vérifier** : ouvrir `https://<votre-user>.github.io/research_news/`
   dans un navigateur. Le digest doit s'afficher.

5. **(Optionnel) Clé API Semantic Scholar** : pour lever les rate-limits
   de S2, créer un secret `S2_API_KEY` dans Settings > Secrets and
   variables > Actions. Le workflow le lit automatiquement. Clé gratuite
   sur <https://www.semanticscholar.org/product/api>.

## Installation sur Android (PWA)

1. Ouvrir `https://<votre-user>.github.io/research_news/` dans Chrome
2. Menu ⋮ > **"Ajouter à l'écran d'accueil"**
3. Une icône "ResearchNews" apparaît — l'ouverture se fait en plein écran,
   sans barre d'URL, comme une app native
4. Le digest se met à jour automatiquement chaque nuit (le service worker
   récupère le dernier `index.html` au lancement)

## Installation sur iOS / ordinateur de bureau

- **iOS Safari** : Partager > "Sur l'écran d'accueil"
- **Chrome/Edge desktop** : barre d'URL > icône installer à droite >
  "Installer"
- L'app s'ouvre dans sa propre fenêtre, hors navigateur

## Configuration

Tout est éditable dans `config.json` :

- `window_days` — fenêtre temporelle en jours
- `max_per_source` — nombre max de résultats par source
- `arxiv_categories` — catégories arXiv interrogées
- `themes` — groupes de mots-clés avec pondération
- `hard_filters.require_any` — termes dont au moins un doit apparaître
- `semantic_scholar_api_key` — clé API (ou via `S2_API_KEY` en env)
- `mailto` — email pour le "polite pool" d'OpenAlex (quotas plus larges)

## Régénérer les icônes

```bash
python3 scripts/make_icons.py
```

Dessine un motif lattice (nœuds + liens) sur fond sombre. Aucune
dépendance (stdlib uniquement). Sortie dans `icons/icon-192.png` et
`icons/icon-512.png`.

## Fichiers

```
research_news/
├── .github/workflows/digest.yml   # workflow GitHub Actions (schedule + manuel)
├── config.json                    # thèmes, sources, fenêtre
├── icons/                         # icônes PWA (192 + 512)
├── public/
│   ├── manifest.json              # manifeste PWA
│   └── sw.js                      # service worker (cache hors-ligne)
├── scripts/
│   └── make_icons.py              # générateur d'icônes (stdlib)
├── research_news.py               # le script principal
└── output/                        # sortie (ignoré par git, peuplé par la CI)
```

## Notes

- **Latence** : le digest a jusqu'à 24h de retard (mise à jour nocturne).
  Pour du fraîcheur immédiate, lancer "Run workflow" manuellement depuis
  l'onglet Actions.
- **Hors-ligne** : après la première visite, le digest reste consultable
  hors-ligne (service worker). Le contenu est celui du dernier run.
- **Semantic Scholar sans clé** est sévèrement rate-limité (429). arXiv +
  OpenAlex couvrent déjà très bien le domaine.
- **Coût** : 0€. GitHub Actions : 2000 min/mois gratuites (un run = ~30s).
  GitHub Pages : gratuit sur repo public.
