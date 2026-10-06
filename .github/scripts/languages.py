#!/usr/bin/env python3
"""Genera tarjetas SVG con el porcentaje de lenguajes usados en mis repositorios.

Suma los bytes por lenguaje (según GitHub Linguist) de:
  * todos los repos propios, públicos y privados (sin forks ni este repo de perfil), y
  * los repos extra de EXTRA_REPOS (por ejemplo, el repo privado de Sentinel).

Variables de entorno:
  GH_TOKEN           token con acceso de lectura a mis repos (obligatorio)
  EXTRA_REPOS        repos extra "owner/nombre", separados por coma o salto de línea
  EXTRA_REPOS_TOKEN  token para los repos extra, si hace falta uno distinto de GH_TOKEN
  EXCLUDE_REPOS      repos propios a ignorar ("nombre" u "owner/nombre")
  EXCLUDE_LANGS      lenguajes a ignorar (por ejemplo "HTML, CSS")
  TOP_LANGS          cantidad de lenguajes a mostrar; el resto se agrupa en "Otros" (8)
  OUTPUT_DIR         carpeta donde se escriben los SVG (assets)

No imprime nombres de repositorios: los logs de Actions de un repo público son públicos.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

API_URL = "https://api.github.com/graphql"

REPO_FIELDS = """
fragment RepoLanguages on Repository {
  nameWithOwner
  isPrivate
  languages(first: 100, orderBy: {field: SIZE, direction: DESC}) {
    edges { size node { name color } }
  }
}
"""

OWN_REPOS_QUERY = REPO_FIELDS + """
query($cursor: String) {
  viewer {
    login
    repositories(first: 100, after: $cursor, ownerAffiliations: OWNER, isFork: false) {
      pageInfo { hasNextPage endCursor }
      nodes { ...RepoLanguages }
    }
  }
}
"""

REPO_QUERY = REPO_FIELDS + """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) { ...RepoLanguages }
}
"""

FALLBACK_COLOR = "#8b949e"
OTHERS_COLOR = "#6e7681"

THEMES = {
    "light": {"bg": "#ffffff", "border": "#d0d7de", "title": "#1f2328", "text": "#59636e", "track": "#eff2f5"},
    "dark": {"bg": "#0d1117", "border": "#3d444d", "title": "#f0f6fc", "text": "#9198a1", "track": "#262c36"},
}

WIDTH = 495
PADDING = 25
ROW_HEIGHT = 25


class GraphQLError(Exception):
    def __init__(self, errors):
        super().__init__(", ".join(sorted({e.get("type", "ERROR") for e in errors})))


def graphql(token, query, variables=None):
    request = urllib.request.Request(
        API_URL,
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "profile-language-stats",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 401:
            sys.exit("El token fue rechazado (401): revisá que el secret exista y no esté vencido.")
        raise
    if payload.get("errors"):
        raise GraphQLError(payload["errors"])
    return payload["data"]


def split_list(value):
    return [item.strip() for item in re.split(r"[,\n]", value or "") if item.strip()]


def fetch_own_repos(token):
    repos, cursor = [], None
    while True:
        viewer = graphql(token, OWN_REPOS_QUERY, {"cursor": cursor})["viewer"]
        page = viewer["repositories"]
        repos.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            return viewer["login"], repos
        cursor = page["pageInfo"]["endCursor"]


def fetch_extra_repo(token, full_name, index):
    # Los errores no muestran el nombre del repo para no filtrarlo en los logs.
    owner, _, name = full_name.partition("/")
    if not owner or not name or "/" in name:
        sys.exit(f"EXTRA_REPOS #{index} no tiene el formato owner/nombre.")
    try:
        repo = graphql(token, REPO_QUERY, {"owner": owner, "name": name})["repository"]
    except GraphQLError as error:
        repo = None
        reason = str(error)
    else:
        reason = "NOT_FOUND"
    if repo is None:
        sys.exit(
            f"No se pudo leer EXTRA_REPOS #{index} ({reason}): revisá el nombre y que el token "
            "tenga acceso (si la organización usa SSO, el token tiene que estar autorizado)."
        )
    return repo


def is_excluded(repo, excluded):
    full_name = repo["nameWithOwner"].lower()
    return full_name in excluded or full_name.split("/")[1] in excluded


def sum_languages(repos, excluded_langs):
    totals = {}
    for repo in repos:
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            if name.lower() in excluded_langs:
                continue
            entry = totals.setdefault(name, {"bytes": 0, "color": edge["node"]["color"] or FALLBACK_COLOR})
            entry["bytes"] += edge["size"]
    return totals


def top_languages(totals, limit):
    total = sum(entry["bytes"] for entry in totals.values())
    if not total:
        return []
    ranked = sorted(totals.items(), key=lambda item: item[1]["bytes"], reverse=True)
    languages = [(name, entry["bytes"] * 100 / total, entry["color"]) for name, entry in ranked[:limit]]
    others = sum(entry["bytes"] for _, entry in ranked[limit:])
    if others:
        languages.append(("Otros", others * 100 / total, OTHERS_COLOR))
    return languages


def format_percent(percent):
    if percent < 0.1:
        return "&lt;0,1%"
    return f"{percent:.1f}".replace(".", ",") + "%"


def render(languages, theme):
    colors = THEMES[theme]
    bar_width = WIDTH - 2 * PADDING
    rows = max((len(languages) + 1) // 2, 1)
    height = 90 + rows * ROW_HEIGHT

    body = []
    if languages:
        body.append(f'<rect x="{PADDING}" y="52" width="{bar_width}" height="8" rx="4" fill="{colors["track"]}"/>')
        body.append('<g clip-path="url(#bar)">')
        x = PADDING
        for _, percent, color in languages:
            width = bar_width * percent / 100
            body.append(f'<rect x="{x:.2f}" y="52" width="{width:.2f}" height="8" fill="{color}"/>')
            x += width
        body.append("</g>")

        # Leyenda en dos columnas, ordenada de arriba hacia abajo.
        for i, (name, percent, color) in enumerate(languages):
            x = PADDING + (i // rows) * bar_width / 2
            y = 92 + (i % rows) * ROW_HEIGHT
            body.append(f'<circle cx="{x + 5:.2f}" cy="{y - 4}" r="5" fill="{color}"/>')
            body.append(
                f'<text x="{x + 16:.2f}" y="{y}" class="lang">{escape(name)} '
                f'<tspan class="percent">{format_percent(percent)}</tspan></text>'
            )
    else:
        body.append(f'<text x="{PADDING}" y="75" class="percent">Todavía no hay datos: ejecutá el workflow.</text>')

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" role="img" aria-labelledby="title">
<title id="title">Lenguajes más usados</title>
<style>
  text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif; }}
  .title {{ font-size: 18px; font-weight: 600; fill: {colors["title"]}; }}
  .lang {{ font-size: 13px; font-weight: 500; fill: {colors["title"]}; }}
  .percent {{ font-size: 13px; font-weight: 400; fill: {colors["text"]}; }}
</style>
<defs><clipPath id="bar"><rect x="{PADDING}" y="52" width="{bar_width}" height="8" rx="4"/></clipPath></defs>
<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1}" rx="6" fill="{colors["bg"]}" stroke="{colors["border"]}"/>
<text x="{PADDING}" y="35" class="title">Lenguajes más usados</text>
{chr(10).join(body)}
</svg>
"""


def main():
    token = os.environ.get("GH_TOKEN")
    if not token:
        sys.exit("Falta GH_TOKEN: creá el secret STATS_TOKEN (ver .github/workflows/languages.yml).")
    extra_token = os.environ.get("EXTRA_REPOS_TOKEN") or token
    excluded_repos = {name.lower() for name in split_list(os.environ.get("EXCLUDE_REPOS"))}
    excluded_langs = {name.lower() for name in split_list(os.environ.get("EXCLUDE_LANGS"))}
    limit = int(os.environ.get("TOP_LANGS") or 8)
    output_dir = Path(os.environ.get("OUTPUT_DIR") or "assets")

    login, own_repos = fetch_own_repos(token)
    excluded_repos.add(f"{login}/{login}".lower())  # el repo de perfil no es un proyecto
    repos = [repo for repo in own_repos if not is_excluded(repo, excluded_repos)]

    seen = {repo["nameWithOwner"].lower() for repo in repos}
    for index, full_name in enumerate(split_list(os.environ.get("EXTRA_REPOS")), start=1):
        repo = fetch_extra_repo(extra_token, full_name, index)
        if repo["nameWithOwner"].lower() not in seen:
            seen.add(repo["nameWithOwner"].lower())
            repos.append(repo)

    languages = top_languages(sum_languages(repos, excluded_langs), limit)

    private = sum(repo["isPrivate"] for repo in repos)
    print(f"Repositorios analizados: {len(repos)} ({private} privados)")
    for name, percent, _ in languages:
        print(f"  {name}: {format_percent(percent).replace('&lt;', '<')}")

    output_dir.mkdir(parents=True, exist_ok=True)
    for theme in THEMES:
        (output_dir / f"languages-{theme}.svg").write_text(render(languages, theme), encoding="utf-8")


if __name__ == "__main__":
    main()
