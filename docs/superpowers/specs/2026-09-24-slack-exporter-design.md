# Slack Exporter — Design

Date : 2026-09-24
Statut : validé en conversation, en attente de revue de la spec écrite

## 1. Objectif

Exporter depuis Slack toutes les conversations accessibles à l'utilisateur
(canaux publics dont il est membre, canaux privés, DM, conversations de groupe),
**pièces jointes comprises**, puis produire une **archive HTML statique consultable**
que l'utilisateur dépose dans Microsoft Teams (fichiers d'un canal) ou OneDrive.

### Contexte et contraintes

- L'utilisateur n'a **aucun droit d'administration**, ni sur Slack ni sur Microsoft 365.
- Un import natif dans Teams (API Graph d'import, `Teamwork.Migrate.All`) exige
  un consentement admin : il est **hors périmètre**. La cible est une archive lisible,
  pas des messages Teams.
- Côté Slack, l'accès se fait avec un token utilisateur :
  - `xoxp-…` (app Slack installée par l'utilisateur) si le workspace l'autorise ;
  - sinon `xoxc-…` + cookie `d` issus de la session navigateur. L'utilisateur est
    responsable de vérifier que cet usage est conforme aux règles de son organisation.
- Scopes nécessaires (mode `xoxp`) : `channels:read`, `channels:history`,
  `groups:read`, `groups:history`, `im:read`, `im:history`, `mpim:read`,
  `mpim:history`, `users:read`, `files:read`.

### Critères de succès

1. `slack-exporter export` récupère toutes les conversations accessibles, leurs fils
   et leurs fichiers, et peut être interrompu puis relancé sans tout refaire.
2. `slack-exporter render` produit un dossier `site/` ouvrable localement
   (`index.html`) sans connexion réseau ni ressource externe.
3. Le rendu affiche auteurs, dates, fils, réactions, pièces jointes et mise en forme
   de façon lisible.
4. Les erreurs sur une conversation ou un fichier n'interrompent pas l'export et
   sont listées à la fin.

## 2. Architecture

Langage : Python ≥ 3.11. Dépendances : `slack_sdk`, `jinja2`, `click`, `requests`
(téléchargement des fichiers). Tests : `pytest`.

```
slack_exporter/
├── pyproject.toml
├── src/slack_exporter/
│   ├── cli.py           # commandes `export` et `render` (click)
│   ├── auth.py          # construit le client selon le type de token
│   ├── slack_api.py     # appels Slack, pagination, retries, téléchargement
│   ├── exporter.py      # orchestration de l'export, reprise
│   ├── storage.py       # lecture/écriture de l'archive sur disque
│   ├── formatting.py    # mrkdwn Slack → HTML (fonctions pures)
│   ├── renderer.py      # génération du site statique
│   └── templates/       # index.html, conversation.html, style.css
└── tests/
```

Frontières :

- `slack_api` est le seul module qui parle à Slack. Il expose des méthodes qui
  renvoient des `dict` Python et des itérateurs paginés ; il est remplaçable par un
  faux client dans les tests.
- `exporter` ne connaît pas le HTML ; `renderer` ne connaît pas Slack. Leur seul
  contrat commun est le format d'archive (section 3), accédé via `storage`.
- `formatting` ne fait aucun I/O. Il reçoit le texte et un contexte de résolution
  (table utilisateurs, table conversations exportées) et renvoie du HTML sûr.

### Interface CLI

```bash
export SLACK_TOKEN=xoxp-...        # ou xoxc-... avec SLACK_COOKIE_D=...
slack-exporter export --out ./archive [--types public,private,im,mpim] \
                      [--since YYYY-MM-DD] [--only <nom|id>]... [--refresh]
slack-exporter render --archive ./archive --site ./site
```

- Par défaut, `--types` vaut les quatre types et tout l'historique est exporté.
- `--only` est répétable et accepte un nom de canal ou un identifiant.
- Le token n'est lu que depuis l'environnement, jamais en argument, pour éviter qu'il
  reste dans l'historique du shell. Il n'est jamais écrit dans l'archive ni dans les logs.

## 3. Format de l'archive

```
archive/
├── meta.json            # workspace (id, nom, url), date d'export, format_version, errors[]
├── users.json           # liste des utilisateurs (réponse brute users.list)
├── channels.json        # conversations exportées : id, nom, type, membres (im/mpim)
├── state.json           # {"completed": [channel_id, ...], "in_progress": channel_id|null}
└── conversations/<channel_id>/
    ├── messages.jsonl           # messages racine, ordre chronologique croissant
    ├── replies/<thread_ts>.jsonl  # réponses d'un fil (hors message parent)
    └── files/<file_id>_<nom_sanitisé>
```

- Les messages sont stockés bruts, tels que renvoyés par l'API, un JSON par ligne.
- `format_version` vaut `1`. `render` refuse une archive de version inconnue.
- Le type de conversation vaut `public`, `private`, `im` ou `mpim`.
- Nom sanitisé : seuls `[A-Za-z0-9._-]` sont gardés (le reste devient `_`),
  au plus 100 caractères, sans `..` ni séparateur de chemin.

## 4. Flux d'export

1. `auth.test` : valide le token, récupère l'identité du workspace et de l'utilisateur.
2. `users.list` (paginé) → `users.json`.
3. `users.conversations` (paginé, `types` selon `--types`, `exclude_archived=false`) :
   uniquement les conversations dont l'utilisateur est membre, sans parcourir tous les
   canaux publics du workspace → écarter celles marquées `is_member: false` → appliquer
   `--only` → `channels.json`.
4. Pour chaque conversation non marquée `completed` dans `state.json` :
   1. marquer `in_progress` ; vider son dossier sauf `files/` ;
   2. `conversations.history` (paginé, `oldest` si `--since`), messages remis en ordre
      chronologique puis écrits dans `messages.jsonl` ;
   3. pour chaque message avec `reply_count > 0` : `conversations.replies` → fichier
      `replies/<thread_ts>.jsonl` (le parent est retiré) ;
   4. pour chaque fichier des messages et réponses : télécharger `url_private_download`
      avec `Authorization: Bearer <token>` (plus le cookie `d` en mode `xoxc`) s'il
      n'existe pas déjà ; ignorer les fichiers externes ou `tombstone` ;
   5. marquer `completed`.
5. Écrire `meta.json` avec la liste des erreurs rencontrées.

Une conversation déjà `completed` n'est pas réinterrogée, mais ses fichiers absents
(échecs précédents) sont retentés à partir des messages stockés. `channels.json` est
fusionné par identifiant avec celui d'un export précédent, pour qu'un export partiel
(`--only`, `--types`) ne masque pas les conversations déjà archivées.
`--refresh` ignore `state.json` et les fichiers déjà présents.

**Mise à jour (`--update`)** : pour chaque conversation `completed`, relire l'historique à
partir de *(dernier message archivé − 30 jours)* et fusionner par `ts`. Les messages
antérieurs à la fenêtre sont conservés tels quels, ceux de la fenêtre sont remplacés par
la version Slack (modifications, réactions, suppressions), et les nouveaux sont ajoutés.
Seuls les fils nouveaux ou dont `latest_reply` a changé sont relus. `messages.jsonl` est
écrit en dernier, pour qu'une interruption laisse l'historique précédent intact.
Les conversations non terminées sont exportées normalement. `--update` est incompatible
avec `--refresh` et `--since`. Ligne finale :
`↻ [i/N] <titre> : +X messages, Y fils mis à jour, Z fichiers`.
La progression affiche l'identité connectée, le nombre d'utilisateurs, le nombre de
conversations à exporter, puis une ligne `✓ [i/N] <titre> : …` par conversation
(canal `#nom`, ou nom de l'interlocuteur pour un DM). Pendant l'export d'une
conversation, une ligne de statut réécrite sur place (`\r`, seulement si la sortie est
un terminal) indique les messages récupérés, les fils traités et les fichiers
téléchargés, avec le nom du fichier en cours. Chaque pause imposée par Slack (HTTP 429)
est annoncée avec sa durée.

## 5. Gestion des erreurs

| Cas | Comportement |
|-----|--------------|
| HTTP 429 | attendre `Retry-After`, réessayer (RetryHandler de `slack_sdk`) |
| Erreur réseau / 5xx | backoff exponentiel, 5 tentatives max |
| `not_in_channel`, `channel_not_found`, `missing_scope` sur une conversation | log d'avertissement, entrée dans `errors`, conversation suivante |
| Fichier 403/404 ou échec après retries | log d'avertissement, entrée dans `errors`, on continue |
| `invalid_auth`, `token_revoked`, `not_authed` | arrêt immédiat, message clair, code de sortie non nul |

Un léger délai entre deux appels à `conversations.history`/`replies` évite de taper
en continu dans la limite de débit (environ 50 requêtes par minute).

## 6. Rendu HTML

```
site/
├── index.html
├── assets/style.css
└── c/<channel_id>/
    ├── index.html              # ou une page par année si > 5 000 messages
    ├── <année>.html
    └── files/…                 # copiés depuis l'archive
```

- **Index** : conversations groupées par type (Canaux publics, Canaux privés,
  Messages directs, Groupes), avec nombre de messages et période couverte.
  Les DM sont nommés d'après l'interlocuteur, les groupes d'après la liste des membres.
- **Page de conversation** : séparateurs par jour. Chaque message affiche le nom de
  l'auteur (ou `username`/`bot_profile` pour les bots), l'heure locale, le texte
  formaté, les pièces jointes, les réactions (`emoji × nombre`) et la mention
  « (modifié) ».
- **Fils** : réponses dans un `<details>` sous le message parent,
  avec le libellé « N réponses ».
- **Pièces jointes** : les images (`mimetype` `image/*`) s'affichent en miniature
  (`<img>` limité en CSS) avec un lien vers l'original ; les autres fichiers
  s'affichent en lien avec leur nom et leur taille. Un fichier absent donne
  « fichier non disponible ».
- **Pas de JavaScript**, sauf sur la page de recherche. Aucune ressource externe.
  Tous les liens sont relatifs.
- **Ancres** : chaque message porte `id="m-<ts avec . remplacé par ->"`.

### Recherche (`search.html`)

- `render` écrit `assets/search-index.js` (`window.SLACK_SEARCH = {...}` : un `.js` et
  non un `.json`, que les navigateurs refusent de lire en `file://`), `assets/search.js` et
  `search.html`, liée depuis l'index et chaque conversation.
- Index : `conversations` = `[{id, title, type}]`, `messages` = une entrée par message et
  par réponse : `[n° de conversation, auteur, ts entier, texte brut, lien, 1 si réponse]`.
  Le texte brut vient de `mrkdwn_to_text` (mentions résolues, sans mise en forme),
  suivi des noms des pièces jointes. Le lien pointe sur la bonne page (année comprise)
  et, pour une réponse, sur l'ancre du message parent.
- Correspondance : tous les termes présents, `"expression"` exacte, sans casse ni accents
  (NFD sans diacritiques). Filtres : type, auteur (sous-chaîne), période en dates
  locales (bornes incluses). Résultats du plus récent au plus ancien, 100 par page,
  termes surlignés. Le texte des messages n'est jamais inséré comme HTML.
- Fonctionne hors ligne en local ; pas dans l'aperçu web de Teams/SharePoint, qui
  bloque les scripts.
- **Pagination** : au-delà de 5 000 messages racine, une page par année et un
  sommaire dans `index.html` de la conversation.

### Conversion mrkdwn (`formatting.py`)

L'échappement HTML du texte brut passe **en premier**. Les transformations suivantes
n'insèrent que du balisage contrôlé.

| Slack | HTML |
|-------|------|
| `<@U123>` / `<@U123\|nom>` | `<span class="mention">@Nom affiché</span>` ; ID inconnu → `@U123` |
| `<#C123\|nom>` | lien vers `../C123/index.html` si exportée, sinon `#nom` |
| `<!here>`, `<!channel>`, `<!everyone>` | `@here`, `@channel`, `@everyone` |
| `<!subteam^S123\|@grp>` | `@grp` |
| `<https://x\|texte>` / `<https://x>` | `<a href="https://x">texte</a>` (schémas `http`, `https`, `mailto` seulement) |
| `` ```bloc``` `` | `<pre><code>` (pas d'autre formatage à l'intérieur) |
| `` `code` `` | `<code>` |
| `*gras*`, `_italique_`, `~barré~` | `<strong>`, `<em>`, `<del>` |
| `&gt; citation` en début de ligne | `<blockquote>` |
| `:smile:` | emoji Unicode via une table intégrée ; inconnu → texte inchangé |
| sauts de ligne | `<br>` |

Messages avec `blocks` ou `attachments` riches : on rend le champ `text`
(texte de secours) ; si `text` est vide, on concatène les `fallback`/`text` des attachments.

## 7. Sécurité

- Autoescape Jinja2 activé ; seul le HTML sorti par `formatting` est marqué `Markup`.
- Les URL de liens sont filtrées par schéma (`http`, `https`, `mailto`).
- Les noms de fichiers sont sanitisés à l'écriture (section 3), et les chemins
  résolus sont vérifiés comme contenus dans le dossier cible.
- Le token n'est jamais écrit sur disque ni journalisé.

## 8. Tests

Approche TDD avec `pytest`. Aucun test n'appelle le vrai Slack.

- `formatting` : un test par règle du tableau, plus les cas limites (ID inconnus,
  formatage dans les blocs de code, `<script>` dans le texte, `javascript:` dans un lien).
- `slack_api` : faux transport ou client simulé ; pagination via
  `response_metadata.next_cursor`, erreurs fatales et non fatales, téléchargement
  avec en-têtes corrects selon le type de token.
- `storage` : aller-retour JSONL, sanitisation des noms, refus des traversées de chemin.
- `exporter` : bout en bout avec un faux `slack_api` vers `tmp_path`, dont les fils,
  les fichiers, l'erreur non bloquante et la reprise après une interruption simulée.
- `renderer` : petite archive de test → vérification des pages générées, des liens
  relatifs résolvables, de l'échappement et de la pagination par année.
- Validation finale manuelle sur le workspace réel de l'utilisateur.

## 9. Hors périmètre

- Import dans Microsoft Teams (repost ou API d'import).
- Détection, par `--update`, de l'activité sur des messages antérieurs à la fenêtre de 30 jours.
- Emojis personnalisés rendus en image (ils gardent leur texte `:nom:`).
- Rendu fidèle des Block Kit et des messages d'apps (seul le texte de secours est rendu).
- Canaux dont l'utilisateur n'est pas membre, et export administrateur du workspace.
