#!/usr/bin/env python3
"""Refresh modules.json from downloadUrl + GitHub + root module.prop."""

from __future__ import annotations

import io
import json
import os
import re
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote
from urllib.request import Request, urlopen

MODULES_FILE = "modules.json"
TOKEN = os.environ.get("GITHUB_TOKEN", "")
PROXY_PREFIX = "https://ghfast.top/"
MAX_ZIP_BYTES = 80 * 1024 * 1024
DOWNLOAD_TIMEOUT = 90
API_TIMEOUT = 30

RELEASE_URL_PATTERN = re.compile(
    r"github\.com/([^/]+)/([^/]+)/releases/download/([^/]+)/(.+)$",
    re.IGNORECASE,
)
REPO_URL_PATTERN = re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+?)/?(?:\.git)?/?$",
    re.IGNORECASE,
)
CHINA_TZ = timezone(timedelta(hours=8))


def github_api(path: str) -> dict | list | None:
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
        with urlopen(req, timeout=API_TIMEOUT) as resp:
            return json.load(resp)
    except HTTPError as exc:
        print(f"  API error {exc.code} for {path}", file=sys.stderr)
    except URLError as exc:
        print(f"  Network error for {path}: {exc.reason}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"  Unexpected error for {path}: {exc}", file=sys.stderr)
    return None


def unwrap_proxy(url: str) -> str:
    u = (url or "").strip()
    if u.startswith(PROXY_PREFIX):
        return u[len(PROXY_PREFIX) :]
    return u


def ensure_proxy(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return u
    if u.startswith(PROXY_PREFIX):
        return u
    if "github.com/" in u or "githubusercontent.com/" in u:
        return PROXY_PREFIX + u
    return u


def download_url_of(module: dict) -> str:
    lr = module.get("latestRelease")
    if isinstance(lr, dict):
        return (lr.get("downloadUrl") or "").strip()
    return (module.get("downloadUrl") or "").strip()


def parse_release_download_url(download_url: str) -> tuple[str, str, str, str] | None:
    match = RELEASE_URL_PATTERN.search(unwrap_proxy(download_url or ""))
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
    return owner, repo, tag, asset_name


def full_name_from_repo_url(repo_url: str) -> str | None:
    match = REPO_URL_PATTERN.match((repo_url or "").strip())
    if not match:
        return None
    owner, repo = match.group(1), match.group(2)
    if repo.endswith(".git"):
        repo = repo[:-4]
    return f"{owner}/{repo}"


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


def parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    v = value.strip().lower()
    if v in {"1", "true", "yes", "y", "on"}:
        return True
    if v in {"0", "false", "no", "n", "off"}:
        return False
    return None


def parse_module_prop(text: str) -> dict[str, str]:
    props: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            props[key] = value.strip()
    return props


def download_bytes(url: str, max_bytes: int = MAX_ZIP_BYTES) -> bytes | None:
    candidates = []
    raw = unwrap_proxy(url)
    proxied = ensure_proxy(raw)
    if proxied != raw:
        candidates.append(proxied)
    candidates.append(raw)

    for candidate in candidates:
        req = Request(
            candidate,
            headers={"User-Agent": "MakoSU-ModuleRefresh/1.0"},
        )
        try:
            with urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
                data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                print(f"  zip too large (>{max_bytes}): {candidate}", file=sys.stderr)
                continue
            return data
        except HTTPError as exc:
            print(f"  download HTTP {exc.code}: {candidate}", file=sys.stderr)
        except URLError as exc:
            print(f"  download network error: {candidate}: {exc.reason}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"  download failed: {candidate}: {exc}", file=sys.stderr)
    return None


def read_root_module_prop(zip_bytes: bytes) -> dict[str, str] | None:
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            # Only root-level module.prop is accepted.
            try:
                with zf.open("module.prop") as fp:
                    return parse_module_prop(fp.read().decode("utf-8", errors="replace"))
            except KeyError:
                # Some zips store with leading ./ 
                for name in zf.namelist():
                    if name.replace("\\", "/") == "./module.prop":
                        with zf.open(name) as fp:
                            return parse_module_prop(
                                fp.read().decode("utf-8", errors="replace")
                            )
                print("  module.prop not at zip root", file=sys.stderr)
                return None
    except zipfile.BadZipFile:
        print("  invalid zip", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"  zip parse error: {exc}", file=sys.stderr)
    return None


def set_if_changed(target: dict, key: str, value, changes: list[str]) -> None:
    old = target.get(key)
    if old != value:
        target[key] = value
        changes.append(f"{key} {old!r}->{value!r}")


def ensure_latest_release(module: dict) -> dict:
    lr = module.get("latestRelease")
    if not isinstance(lr, dict):
        lr = {}
        module["latestRelease"] = lr
    return lr


def apply_download_derived_fields(module: dict, changes: list[str]) -> tuple[str, str, str] | None:
    """Fill repoUrl/author link/proxy downloadUrl from downloadUrl. Returns owner/repo/tag/asset or None."""
    download_url = download_url_of(module)
    if not download_url:
        return None

    proxied = ensure_proxy(download_url)
    lr = ensure_latest_release(module)
    if (lr.get("downloadUrl") or "").strip() != proxied:
        old = lr.get("downloadUrl")
        lr["downloadUrl"] = proxied
        changes.append(f"downloadUrl {old!r}->{proxied!r}")

    parsed = parse_release_download_url(proxied)
    if not parsed:
        print("  cannot parse GitHub release downloadUrl", file=sys.stderr)
        return None

    owner, repo, tag, asset_name = parsed
    repo_url = f"https://github.com/{owner}/{repo}"
    set_if_changed(module, "repoUrl", repo_url, changes)

    authors = module.get("authors")
    if not isinstance(authors, list) or not authors:
        authors = [{"name": owner, "link": f"https://github.com/{owner}"}]
        module["authors"] = authors
        changes.append(f"authors -> [{{name:{owner}}}]")
    else:
        first = authors[0] if isinstance(authors[0], dict) else {}
        if not isinstance(first, dict):
            first = {}
            authors[0] = first
        old_link = (first.get("link") or "").strip()
        new_link = f"https://github.com/{owner}"
        if old_link != new_link:
            first["link"] = new_link
            changes.append(f"author.link {old_link!r}->{new_link!r}")
        if not (first.get("name") or "").strip():
            first["name"] = owner
            changes.append(f"author.name -> {owner}")

    return owner, repo, tag, asset_name


def apply_repo_about(module: dict, repo_data: dict | None, changes: list[str]) -> None:
    if not isinstance(repo_data, dict):
        return
    desc = (repo_data.get("description") or "").strip()
    if desc:
        set_if_changed(module, "summary", desc, changes)
    stars = repo_data.get("stargazers_count")
    if stars is not None:
        old = int(module.get("stargazerCount") or 0)
        new = int(stars)
        if old != new:
            module["stargazerCount"] = new
            changes.append(f"stargazerCount {old}->{new}")


def apply_release_meta(
    module: dict,
    release: dict | None,
    asset_name: str,
    changes: list[str],
) -> None:
    if not isinstance(release, dict):
        return
    lr = ensure_latest_release(module)
    release_date = format_release_date(
        release.get("published_at") or release.get("created_at") or ""
    )
    if release_date:
        set_if_changed(lr, "time", release_date, changes)

    asset = find_asset(release, asset_name)
    if not asset:
        print(f"  asset not found: {asset_name}", file=sys.stderr)
        return
    size = int(asset.get("size") or 0)
    downloads = int(asset.get("download_count") or 0)
    old_size = int(lr.get("size") or 0)
    old_downloads = int(lr.get("downloadCount") or 0)
    if old_size != size:
        lr["size"] = size
        changes.append(f"size {old_size}->{size}")
    else:
        lr["size"] = size
    if old_downloads != downloads:
        lr["downloadCount"] = downloads
        changes.append(f"downloadCount {old_downloads}->{downloads}")
    else:
        lr["downloadCount"] = downloads


def apply_module_prop(module: dict, props: dict[str, str], changes: list[str]) -> None:
    if "id" in props and props["id"]:
        set_if_changed(module, "moduleId", props["id"], changes)
    if "name" in props and props["name"]:
        set_if_changed(module, "moduleName", props["name"], changes)

    if "version" in props and props["version"]:
        lr = ensure_latest_release(module)
        set_if_changed(lr, "name", props["version"], changes)

    if "author" in props and props["author"]:
        authors = module.get("authors")
        if not isinstance(authors, list) or not authors:
            authors = [{}]
            module["authors"] = authors
        first = authors[0] if isinstance(authors[0], dict) else {}
        if not isinstance(first, dict):
            first = {}
            authors[0] = first
        old = (first.get("name") or "").strip()
        new = props["author"]
        if old != new:
            first["name"] = new
            changes.append(f"author.name {old!r}->{new!r}")

    if "metamodule" in props:
        parsed = parse_bool(props.get("metamodule"))
        if parsed is not None:
            old = bool(module.get("metamodule", False))
            if old != parsed:
                module["metamodule"] = parsed
                changes.append(f"metamodule {old}->{parsed}")
            else:
                module["metamodule"] = parsed


def refresh_module(
    module: dict,
    repo_cache: dict[str, dict | None],
    release_cache: dict[tuple[str, str], dict | None],
) -> list[str]:
    changes: list[str] = []
    label = module.get("moduleName") or module.get("moduleId") or "?"

    # Drop legacy fields
    module.pop("updatedAt", None)
    module.pop("createdAt", None)
    module.pop("versionCode", None)
    lr = module.get("latestRelease")
    if isinstance(lr, dict):
        lr.pop("versionCode", None)

    download_url = download_url_of(module)
    if not download_url:
        print(f"[{label}] skip: empty downloadUrl")
        return changes

    print(f"[{label}] refresh from downloadUrl")
    parsed = apply_download_derived_fields(module, changes)
    if not parsed:
        return changes

    owner, repo, tag, asset_name = parsed
    full_name = f"{owner}/{repo}"

    if full_name not in repo_cache:
        repo_cache[full_name] = github_api(f"/repos/{full_name}")  # type: ignore[assignment]
        if repo_cache[full_name] is None:
            print(f"  repo about failed: {full_name}", file=sys.stderr)
        else:
            stars = repo_cache[full_name].get("stargazers_count")  # type: ignore[union-attr]
            print(f"  repo stars={stars} about={(repo_cache[full_name].get('description') or '')[:80]}")  # type: ignore[union-attr]
    apply_repo_about(module, repo_cache.get(full_name), changes)

    key = (full_name, tag)
    if key not in release_cache:
        release_cache[key] = github_api(f"/repos/{full_name}/releases/tags/{tag}")  # type: ignore[assignment]
        if release_cache[key] is None:
            print(f"  release failed: {full_name}@{tag}", file=sys.stderr)
        else:
            published = format_release_date(
                (release_cache[key] or {}).get("published_at")  # type: ignore[union-attr]
                or (release_cache[key] or {}).get("created_at")  # type: ignore[union-attr]
                or ""
            )
            print(f"  release published={published or '?'}")
    apply_release_meta(module, release_cache.get(key), asset_name, changes)

    zip_bytes = download_bytes(download_url_of(module))
    if zip_bytes is None:
        print("  zip download failed; keep existing prop-derived fields", file=sys.stderr)
        return changes

    props = read_root_module_prop(zip_bytes)
    if not props:
        print("  root module.prop missing; keep existing prop-derived fields", file=sys.stderr)
        return changes

    print(
        "  prop:"
        f" id={props.get('id','?')}"
        f" name={props.get('name','?')}"
        f" version={props.get('version','?')}"
        f" author={props.get('author','?')}"
        f" metamodule={props.get('metamodule','?')}"
    )
    apply_module_prop(module, props, changes)
    return changes


def order_module(module: dict) -> dict:
    lr = module.get("latestRelease")
    ordered_lr = None
    if isinstance(lr, dict):
        lr_keys = ["name", "time", "downloadUrl", "size", "downloadCount"]
        ordered_lr = {k: lr[k] for k in lr_keys if k in lr}
        for k, v in lr.items():
            if k not in ordered_lr:
                ordered_lr[k] = v

    keys = [
        "moduleId",
        "moduleName",
        "authors",
        "summary",
        "metamodule",
        "stargazerCount",
        "latestRelease",
        "repoUrl",
    ]
    out: dict = {}
    for key in keys:
        if key == "latestRelease" and ordered_lr is not None:
            out[key] = ordered_lr
        elif key in module:
            out[key] = module[key]
    for key, value in module.items():
        if key not in out:
            out[key] = value
    return out


def main() -> int:
    path = Path(MODULES_FILE)
    modules = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(modules, list):
        print("modules.json must be a JSON array", file=sys.stderr)
        return 1

    repo_cache: dict[str, dict | None] = {}
    release_cache: dict[tuple[str, str], dict | None] = {}
    changed = 0

    for module in modules:
        if not isinstance(module, dict):
            continue
        label = module.get("moduleName") or module.get("moduleId") or "?"
        changes = refresh_module(module, repo_cache, release_cache)
        if changes:
            changed += 1
            print(f"[{label}] " + ", ".join(changes))
        else:
            print(f"[{label}] unchanged")

    ordered = [order_module(m) if isinstance(m, dict) else m for m in modules]
    path.write_text(
        json.dumps(ordered, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"\nUpdated {changed} module(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
