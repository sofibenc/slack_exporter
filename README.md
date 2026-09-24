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

L'outil utilise votre session Slack dans le navigateur : un token `xoxc-…` et le cookie `d`.

1. Ouvrez votre workspace Slack **dans un navigateur** (Chrome, Edge ou Firefox, pas
   l'application de bureau), puis les outils de développement (F12).
2. Dans l'onglet *Console*, exécutez :
   ```js
   Object.values(JSON.parse(localStorage.localConfig_v2).teams).map(t => [t.name, t.url, t.token])
   ```
   Copiez le token `xoxc-…` de la ligne correspondant à votre workspace
   (cette méthode peut changer avec les versions de Slack).
3. Dans *Application* (Chrome, Edge) ou *Stockage* (Firefox) → *Cookies* →
   `https://app.slack.com`, **décochez « Show URL-decoded »** si la case existe, puis copiez
   la valeur du cookie nommé exactement `d`. Elle commence par `xoxd-` et contient des `%2F`.

Le token reste valable tant que la session est ouverte : se déconnecter de Slack dans ce
navigateur l'invalide.

## Utilisation

```bash
read -rs SLACK_TOKEN && export SLACK_TOKEN          # Entrée, collez le token xoxc-…, Entrée
read -rs SLACK_COOKIE_D && export SLACK_COOKIE_D    # Entrée, collez le cookie xoxd-…, Entrée

.venv/bin/slack-exporter export --out ./archive
.venv/bin/slack-exporter render --archive ./archive --site ./site
```

Ouvrez ensuite `site/index.html`.

- L'export est **reprenable** : en cas d'interruption, relancez la même commande.
  Les conversations terminées sont sautées et les fichiers déjà téléchargés sont conservés.
- Options d'`export` : `--types public,private,im,mpim`, `--since AAAA-MM-JJ`,
  `--only <nom|id>` (répétable) et `--refresh` pour tout reprendre de zéro.

## Exporter les conversations privées

Pour n'exporter que vos messages directs (`im`) et vos conversations de groupe (`mpim`) :

```bash
.venv/bin/slack-exporter export --out ./archive --types im,mpim
```

Pour un seul message direct, passez son identifiant (commençant par `D`) à `--only` :
`--only` n'accepte pas le nom d'une personne, car un message direct n'a pas de nom
côté Slack. L'identifiant figure dans l'URL de la conversation dans Slack
(`…/client/T…/D0XXXXXXX`) ou dans `archive/channels.json` après un premier export.

```bash
.venv/bin/slack-exporter export --out ./archive --only D0XXXXXXX
```

Ajoutez `private` à `--types` pour inclure aussi les canaux privés :
`--types private,im,mpim`.

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

## Rechercher dans les conversations

Le lien **🔍 Rechercher**, en haut de chaque page du site, ouvre `site/search.html` :

- tous les mots doivent être présents, `"une expression"` cherche l'expression exacte ;
- sans tenir compte des majuscules ni des accents (`reunion` trouve « Réunion ») ;
- filtres par type (canaux publics, canaux privés, messages directs, groupes), par personne
  et par période ;
- la recherche porte sur les messages, les réponses dans les fils et les noms des pièces
  jointes ; chaque résultat mène au message dans sa conversation.

La recherche fonctionne hors ligne, **site ouvert depuis l'ordinateur** (dossier décompressé
ou synchronisé avec OneDrive). Elle ne fonctionne pas dans l'aperçu web de Teams ou
SharePoint, qui bloque les scripts. Après un `export --update`, relancez `render` pour
mettre l'index à jour.

## Déposer l'archive dans Teams

Zippez le dossier `site/`, puis déposez-le dans l'onglet *Fichiers* d'un canal Teams
ou dans OneDrive. Une fois décompressé, ouvrez `index.html` dans le navigateur.
Tous les liens sont relatifs et le site n'utilise aucune ressource externe.

## Tests

```bash
.venv/bin/pytest
```
