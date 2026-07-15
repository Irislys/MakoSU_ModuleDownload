#!/usr/bin/env python3
"""Refresh modules.json stars, release time, size and download counts from GitHub."""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import unquote
from urllib.request import Request, urlopen

MODULES_FILE = "modules.json"
TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO_URL_PATTERN = re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+?)/?(?:\.git)?/?$",
    re.IGNORECASE,
)
RELEASE_URL_PATTERN = re.compile(
    r"github\.com/([^/]+)/([^/]+)/releases/download/([^/]+)/(.+)$",
    re.IGNORECASE,
)
CHINA_TZ = timezone(timedelta(hours=8))


def github_api(path: str) -> dict | None:
    req = Request(
        f"https://api.github.com{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "MakoSU-ModuleRefresh/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except HTTPError as exc:
        print(f"  API error {exc.code} for {path}", file=sys.stderr)
    except URLError as exc:
        print(f"  Network error for {path}: {exc.reason}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"  Unexpected error for {path}: {exc}", file=sys.stderr)
    return None


def full_name_from_repo_url(repo_url: str) -> str | None:
    match = REPO_URL_PATTERN.match((repo_url or "").strip())
    if not match:
        return None
    owner, repo = match.group(1), match.group(2)
    if repo.endswith(".git"):
        repo = repo[:-4]
    return f"{owner}/{repo}"


def download_url_of(module: dict) -> str:
    lr = module.get("latestRelease")
    if isinstance(lr, dict):
        return (lr.get("downloadUrl") or "").strip()
    return (module.get("downloadUrl") or "").strip()


def parse_release_download_url(download_url: str) -> tuple[str, str, str] | None:
    match = RELEASE_URL_PATTERN.search(download_url or "")
    if not match:
        return None
    owner, repo, tag, asset_name = (
        match.group(1),
        match.group(2),
        match.group(3),
        unquote(match.group(4)),
    )
    if repo.endswith(".git"):
        repo = repo[:-4]
    return f"{owner}/{repo}", tag, asset_name


def format_release_date(iso_time: str) -> str | None:
    if not iso_time:
        return None
    try:
        dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
        return dt.astimezone(CHINA_TZ).strftime("%Y-%m-%d")
    except ValueError:
        return None


def find_asset(release: dict, asset_name: str) -> dict | None:
    assets = release.get("assets") or []
    for asset in assets:
        if asset.get("name") == asset_name:
            return asset
    for asset in assets:
        url = asset.get("browser_download_url") or ""
        if url.endswith("/" + asset_name) or unquote(url).endswith("/" + asset_name):
            return asset
    return None


def main() -> int:
    with open(MODULES_FILE, encoding="utf-8") as f:
        modules = json.load(f)

    unique_repos: dict[str, str] = {}
    unique_releases: dict[tuple[str, str], None] = {}

    for module in modules:
        name = module.get("moduleName") or module.get("moduleId") or "?"
        # Drop legacy fields if present
        module.pop("updatedAt", None)
        module.pop("createdAt", None)

        repo_url = (module.get("repoUrl") or "").strip()
        if repo_url:
            full_name = full_name_from_repo_url(repo_url)
            if full_name:
                unique_repos.setdefault(repo_url, full_name)
            else:
                print(f"[{name}] invalid repoUrl: {repo_url}")
        else:
            print(f"[{name}] missing repoUrl")

        parsed = parse_release_download_url(download_url_of(module))
        if parsed:
            full_name, tag, _ = parsed
            unique_releases.setdefault((full_name, tag), None)
        else:
            print(f"[{name}] invalid downloadUrl for release metadata")

    print(f"Fetching stars for {len(unique_repos)} unique repo(s)")
    stars_by_repo_url: dict[str, int | None] = {}
    for repo_url, full_name in unique_repos.items():
        data = github_api(f"/repos/{full_name}")
        stars = data.get("stargazers_count") if data else None
        stars_by_repo_url[repo_url] = int(stars) if stars is not None else None
        if stars is None:
            print(f"[{full_name}] stars failed")
        else:
            print(f"[{full_name}] stars={stars}")

    print(f"Fetching release metadata for {len(unique_releases)} unique release(s)")
    release_cache: dict[tuple[str, str], dict | None] = {}
    for full_name, tag in unique_releases:
        data = github_api(f"/repos/{full_name}/releases/tags/{tag}")
        release_cache[(full_name, tag)] = data
        if data is None:
            print(f"[{full_name}@{tag}] release failed")
        else:
            published = format_release_date(
                data.get("published_at") or data.get("created_at") or ""
            )
            print(
                f"[{full_name}@{tag}] published={published or '?'} "
                f"assets={len(data.get('assets') or [])}"
            )

    changed = 0
    for module in modules:
        name = module.get("moduleName") or module.get("moduleId") or "?"
        changes: list[str] = []

        repo_url = (module.get("repoUrl") or "").strip()
        if repo_url and repo_url in stars_by_repo_url:
            stars = stars_by_repo_url[repo_url]
            if stars is not None:
                old_stars = int(module.get("stargazerCount") or 0)
                module["stargazerCount"] = stars
                if old_stars != stars:
                    changes.append(f"stargazerCount {old_stars}->{stars}")

        parsed = parse_release_download_url(download_url_of(module))
        if parsed:
            full_name, tag, asset_name = parsed
            release = release_cache.get((full_name, tag))
            if release:
                release_date = format_release_date(
                    release.get("published_at") or release.get("created_at") or ""
                )
                lr = module.get("latestRelease")
                if not isinstance(lr, dict):
                    lr = {}
                    module["latestRelease"] = lr

                if release_date:
                    old_time = (lr.get("time") or "").strip()
                    lr["time"] = release_date
                    if old_time != release_date:
                        changes.append(f"time {old_time or '?'}->{release_date}")

                asset = find_asset(release, asset_name)
                if asset:
                    size = int(asset.get("size") or 0)
                    downloads = int(asset.get("download_count") or 0)
                    old_size = int(lr.get("size") or 0)
                    old_downloads = int(lr.get("downloadCount") or 0)
                    lr["size"] = size
                    lr["downloadCount"] = downloads
                    if old_size != size:
                        changes.append(f"size {old_size}->{size}")
                    if old_downloads != downloads:
                        changes.append(f"downloadCount {old_downloads}->{downloads}")
                else:
                    print(f"[{name}] asset not found: {asset_name}")

        if changes:
            changed += 1
            print(f"[{name}] " + ", ".join(changes))
        else:
            print(f"[{name}] unchanged")

    with open(MODULES_FILE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(modules, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"\nUpdated {changed} module(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
