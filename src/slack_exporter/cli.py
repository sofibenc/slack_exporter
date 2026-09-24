"""Interface en ligne de commande : `slack-exporter export` et `slack-exporter render`."""
from __future__ import annotations

import os
import shutil
import sys
from datetime import timezone
from pathlib import Path
from typing import TextIO

import click

from slack_exporter.auth import ConfigError, build_api, credentials_from_env
from slack_exporter.exporter import ALL_TYPES, ExportOptions
from slack_exporter.exporter import export as run_export
from slack_exporter.renderer import RenderError
from slack_exporter.renderer import render as run_render
from slack_exporter.slack_api import AuthError
from slack_exporter.storage import Archive


class StatusLine:
    """Ligne de statut réécrite sur place (`\\r`), affichée seulement dans un terminal."""

    def __init__(self, stream: TextIO, width: int | None = None) -> None:
        self._stream = stream
        self._enabled = stream.isatty()
        # Marge de 2 colonnes : l'emoji ⏳ occupe deux colonnes à l'écran.
        self._width = (width or shutil.get_terminal_size().columns) - 2
        self._current = ""

    def update(self, status: str) -> None:
        if not self._enabled:
            return
        status = status[: self._width]
        self._stream.write("\r" + status.ljust(len(self._current)))
        self._stream.flush()
        self._current = status

    def line(self, message: str) -> None:
        """Ligne définitive : remplace le statut, qui est oublié."""
        self._clear()
        self._current = ""
        self._write_line(message)

    def note(self, message: str) -> None:
        """Ligne ponctuelle (pause Slack…) : le statut est réaffiché en dessous."""
        status = self._current
        self._clear()
        self._current = ""
        self._write_line(message)
        if status:
            self.update(status)

    def _clear(self) -> None:
        if self._enabled and self._current:
            self._stream.write("\r" + " " * len(self._current) + "\r")

    def _write_line(self, message: str) -> None:
        self._stream.write(message + "\n")
        self._stream.flush()


def _parse_types(value: str) -> tuple[str, ...]:
    types = tuple(t.strip() for t in value.split(",") if t.strip())
    unknown = [t for t in types if t not in ALL_TYPES]
    if not types or unknown:
        raise click.BadParameter(
            f"valeur invalide {value!r} ; types possibles : {', '.join(ALL_TYPES)}",
            param_hint="--types",
        )
    return types


@click.group()
def main() -> None:
    """Exporte vos conversations Slack et les convertit en archive HTML consultable."""


@main.command()
@click.option("--out", "out_dir", required=True,
              type=click.Path(file_okay=False, path_type=Path),
              help="Dossier de l'archive (créé si besoin, réutilisé pour reprendre).")
@click.option("--types", default=",".join(ALL_TYPES), show_default=True,
              help="Types de conversations, séparés par des virgules.")
@click.option("--since", type=click.DateTime(formats=["%Y-%m-%d"]), default=None,
              help="N'exporter que les messages à partir de cette date (AAAA-MM-JJ, UTC).")
@click.option("--only", multiple=True,
              help="Nom ou identifiant d'une conversation à exporter (répétable).")
@click.option("--refresh", is_flag=True,
              help="Tout réexporter, y compris les conversations et fichiers déjà récupérés.")
@click.option("--update", is_flag=True,
              help="Mettre à jour les conversations déjà exportées : nouveaux messages, et "
                   "modifications des 30 derniers jours.")
def export(
    out_dir: Path, types: str, since, only: tuple[str, ...], refresh: bool, update: bool
) -> None:
    """Exporte les conversations depuis Slack vers OUT_DIR.

    Le token est lu dans SLACK_TOKEN (xoxp-… ou xoxc-…), et le cookie de session
    dans SLACK_COOKIE_D pour un token xoxc-.
    """
    if update and refresh:
        raise click.UsageError("--update est incompatible avec --refresh (qui relit déjà tout).")
    if update and since:
        raise click.UsageError("--update est incompatible avec --since.")
    try:
        credentials = credentials_from_env(os.environ)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    options = ExportOptions(
        types=_parse_types(types),
        since=since.replace(tzinfo=timezone.utc).timestamp() if since else None,
        only=tuple(o.lstrip("#") for o in only),
        refresh=refresh,
        update=update,
    )
    try:
        status = StatusLine(sys.stdout)
        api = build_api(credentials, notify=status.note)
        result = run_export(
            api, Archive(out_dir), options, log=status.line, progress=status.update
        )
    except AuthError as exc:
        raise click.ClickException(
            f"Slack a refusé l'authentification ({exc.code}). Vérifiez SLACK_TOKEN et SLACK_COOKIE_D."
        ) from exc
    click.echo(
        f"Export terminé : {result.conversations} conversations, "
        f"{result.messages} messages, {result.files} fichiers."
    )
    if result.errors:
        click.echo(f"{len(result.errors)} erreur(s), détaillées dans {out_dir / 'meta.json'} :", err=True)
        for error in result.errors:
            target = f"{error['channel']} / fichier {error['file']}" if "file" in error else error["channel"]
            click.echo(f"  - {target} : {error['error']}", err=True)


@main.command()
@click.option("--archive", "archive_dir", required=True,
              type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="Dossier produit par `export`.")
@click.option("--site", "site_dir", required=True,
              type=click.Path(file_okay=False, path_type=Path),
              help="Dossier du site HTML à générer.")
def render(archive_dir: Path, site_dir: Path) -> None:
    """Génère le site HTML consultable à partir d'une archive."""
    try:
        count = run_render(archive_dir, site_dir)
    except RenderError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"{count} conversations rendues : ouvrez {site_dir / 'index.html'}")
