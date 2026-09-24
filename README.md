# slack-exporter

Exporte toutes vos conversations Slack (canaux publics dont vous êtes membre,
canaux privés, messages directs, groupes), pièces jointes comprises, puis génère
une archive HTML consultable hors ligne, à déposer dans Microsoft Teams ou OneDrive.

## Installation

```bash
uv venv .venv && uv pip install -e '.[dev]'
# ou : python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

## Obtenir un token

**Option 1 : token d'app utilisateur (`xoxp-…`)**, si votre workspace autorise l'installation d'apps.

1. Sur <https://api.slack.com/apps>, cliquez sur *Create New App* → *From scratch* et choisissez votre workspace.
2. Dans *OAuth & Permissions* → *User Token Scopes*, ajoutez :
   `channels:read`, `channels:history`, `groups:read`, `groups:history`,
   `im:read`, `im:history`, `mpim:read`, `mpim:history`, `users:read`, `files:read`.
3. Cliquez sur *Install to Workspace*, puis copiez le *User OAuth Token*.

**Option 2 : session navigateur (`xoxc-…` + cookie `d`)**, si l'installation d'apps est bloquée.
Vérifiez d'abord que cet usage est conforme aux règles de votre organisation.

1. Ouvrez Slack dans le navigateur, puis les outils de développement (F12).
2. Dans la console, exécutez
   `JSON.parse(localStorage.localConfig_v2).teams[Object.keys(JSON.parse(localStorage.localConfig_v2).teams)[0]].token` :
   le résultat est le token `xoxc-…` (cette méthode peut changer avec les versions de Slack).
3. Dans *Application* → *Cookies* → `https://app.slack.com`, copiez la valeur du cookie `d` (commence par `xoxd-`).

## Utilisation

```bash
export SLACK_TOKEN=xoxp-...          # ou xoxc-...
export SLACK_COOKIE_D=xoxd-...       # seulement avec un token xoxc-

.venv/bin/slack-exporter export --out ./archive
.venv/bin/slack-exporter render --archive ./archive --site ./site
```

Ouvrez ensuite `site/index.html`.

- L'export est **reprenable** : en cas d'interruption, relancez la même commande.
  Les conversations terminées sont sautées et les fichiers déjà téléchargés sont conservés.
- Options d'`export` : `--types public,private,im,mpim`, `--since AAAA-MM-JJ`,
  `--only <nom|id>` (répétable) et `--refresh` pour tout reprendre de zéro.

## Mettre l'archive à jour

Une relance simple **ne relit pas** les conversations déjà exportées. Pour récupérer ce
qui s'est passé depuis, utilisez `--update` :

```bash
.venv/bin/slack-exporter export --out ./archive --update
.venv/bin/slack-exporter render --archive ./archive --site ./site
```

Pour chaque conversation déjà exportée, `--update` relit les 30 jours précédant le
dernier message archivé. Il ajoute les nouveaux messages, met à jour les messages
modifiés ou supprimés dans cette période, relit les fils qui ont reçu de nouvelles
réponses et télécharge les nouvelles pièces jointes. Les nouvelles conversations sont
exportées entièrement.

**Limite :** l'activité sur des messages plus anciens (réaction ou réponse tardive dans
un vieux fil) n'est vue que par une relecture complète : supprimez `archive/state.json`
puis relancez `export`, sans `--refresh` pour ne pas retélécharger les fichiers.
- Les erreurs non bloquantes (conversation inaccessible, fichier introuvable) sont
  listées à la fin, dans `archive/meta.json` et sur la page d'accueil du site.

## Déposer l'archive dans Teams

Zippez le dossier `site/`, puis déposez-le dans l'onglet *Fichiers* d'un canal Teams
ou dans OneDrive. Une fois décompressé, ouvrez `index.html` dans le navigateur.
Tous les liens sont relatifs et la page n'utilise ni JavaScript ni ressource externe.

## Tests

```bash
.venv/bin/pytest
```
