#!/usr/bin/env python3
"""Convert the maintained catalog to the KernelSU catalog/detail protocol."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
MODULES_FILE = ROOT / "modules.json"
DETAIL_DIR = ROOT / "module"
SITE_DIR = ROOT / "site"


def direct_url(url: str) -> str:
    prefix = "https://ghfast.top/"
    return (url or "").strip().removeprefix(prefix)


def iso_date(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if "T" in raw:
        return raw.replace("+00:00", "Z")
    return f"{raw}T00:00:00Z"


def version_code(value: str) -> str:
    matches = re.findall(r"\d+", value or "")
    return matches[-1] if matches else "0"


def release_tag(url: str, fallback: str) -> str:
    match = re.search(r"/releases/download/([^/]+)/", url or "")
    return unquote(match.group(1)) if match else fallback


def asset_from_release(release: dict, module_id: str) -> dict:
    url = direct_url(release.get("downloadUrl", ""))
    name = unquote(url.rsplit("/", 1)[-1]) or f"{module_id}.zip"
    return {
        "name": name,
        "contentType": "application/zip",
        "downloadUrl": url,
        "downloadCount": int(release.get("downloadCount", 0) or 0),
        "size": int(release.get("size", 0) or 0),
    }


def render_readme(module: dict) -> tuple[str, str]:
    name = html.escape(str(module.get("moduleName", "")))
    summary = html.escape(str(module.get("summary", "")))
    markdown = f"# {module.get('moduleName', '')}\n\n{module.get('summary', '')}\n"
    rendered = f"<h1>{name}</h1>\n<p>{summary}</p>"
    return markdown, rendered


def normalize_module(module: dict) -> tuple[dict, dict]:
    module_id = str(module.get("moduleId", "")).strip()
    if not module_id:
        raise ValueError("moduleId is required")
    release = dict(module.get("latestRelease") or {})
    name = str(release.get("name") or release.get("version") or module_id)
    release_time = iso_date(str(release.get("time", "")))
    repo_url = str(module.get("repoUrl", "")).strip()
    readme, readme_html = render_readme(module)
    asset = asset_from_release(release, module_id)
    tag = release_tag(asset["downloadUrl"], f"v{version_code(name)}")
    if repo_url and "/releases/" not in repo_url:
        release_url = f"{repo_url.rstrip('/')}/releases/tag/{tag}"
    else:
        release_url = repo_url

    detail_release = {
        "name": name,
        "url": release_url,
        "descriptionHTML": f"<p>{html.escape(str(module.get('summary', '')))}</p>",
        "createdAt": release_time,
        "publishedAt": release_time,
        "updatedAt": release_time,
        "tagName": tag,
        "isPrerelease": False,
        "releaseAssets": [asset],
        "version": name,
        "versionCode": version_code(name),
    }

    common = {
        "moduleId": module_id,
        "moduleName": str(module.get("moduleName", "")).strip(),
        "authors": module.get("authors") or [],
        "summary": module.get("summary") or "",
        "updatedAt": release_time,
        "createdAt": release_time,
        "stargazerCount": int(module.get("stargazerCount", 0) or 0),
        "metamodule": bool(module.get("metamodule", False)),
        "repoUrl": repo_url,
        "latestRelease": {
            "name": name,
            "time": release_time,
            "version": name,
            "versionCode": version_code(name),
            "downloadUrl": asset["downloadUrl"],
        },
    }
    detail = {
        **common,
        "url": f"https://irislys.github.io/MakoSU_ModuleDownload/module/{module_id}/",
        "homepageUrl": repo_url,
        "sourceUrl": repo_url,
        "latestRelease": name,
        "latestReleaseTime": release_time,
        "latestBetaReleaseTime": None,
        "latestSnapshotReleaseTime": None,
        "readme": readme,
        "readmeHTML": readme_html,
        "releases": [detail_release],
    }
    return common, detail


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    source = json.loads(MODULES_FILE.read_text(encoding="utf-8"))
    catalog = []
    details = []
    for item in source:
        common, detail = normalize_module(item)
        catalog.append(common)
        details.append(detail)
        write_json(DETAIL_DIR / f"{detail['moduleId']}.json", detail)
    write_json(MODULES_FILE, catalog)
    generate_site(catalog, details)


def generate_site(catalog: list[dict], details: list[dict]) -> None:
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    write_json(SITE_DIR / "modules.json", catalog)
    for detail in details:
        write_json(SITE_DIR / "module" / f"{detail['moduleId']}.json", detail)
    page_script = """
const root = document.querySelector('#app');
const id = location.pathname.match(/module\/([^/]+)/)?.[1];
const esc = value => String(value ?? '').replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));
async function main() {
  const response = await fetch(id ? `module/${encodeURIComponent(id)}.json` : 'modules.json');
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const data = await response.json();
  if (id) {
    root.innerHTML = `<p><a href="../">&larr; All modules</a></p><h1>${esc(data.moduleName)}</h1><p>${esc(data.summary)}</p><nav><a href="${esc(data.homepageUrl)}">Homepage</a> <a href="${esc(data.sourceUrl)}">Source</a></nav><section class="readme">${data.readmeHTML || '<p>No README provided.</p>'}</section><h2>Releases</h2>${(data.releases || []).map(r => `<article><h3>${esc(r.name)} <small>${esc(r.tagName)}</small></h3><p>${esc(r.publishedAt || '')}</p>${r.descriptionHTML || ''}<ul>${(r.releaseAssets || []).map(a => `<li><a href="${esc(a.downloadUrl)}">${esc(a.name)}</a> <small>${Number(a.size || 0).toLocaleString()} bytes, ${Number(a.downloadCount || 0).toLocaleString()} downloads</small></li>`).join('')}</ul></article>`).join('')}`;
  } else {
    root.innerHTML = `<h1>MakoSU Modules</h1><p>KernelSU-compatible module directory.</p><div class="grid">${data.map(m => `<a class="card" href="module/${encodeURIComponent(m.moduleId)}/"><strong>${esc(m.moduleName)}</strong><span>${esc(m.summary)}</span><small>${esc(m.latestRelease?.name || '')} · ★ ${Number(m.stargazerCount || 0).toLocaleString()}</small></a>`).join('')}</div>`;
  }
}
main().catch(error => { root.innerHTML = `<h1>Unable to load modules</h1><pre>${esc(error.message)}</pre>`; });
"""
    style = "body{font:16px system-ui,sans-serif;max-width:1000px;margin:0 auto;padding:32px;color:#24312d;background:#f6faf7}a{color:#146b4c}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}.card,article{display:flex;flex-direction:column;gap:8px;padding:18px;border:1px solid #cfe3d7;border-radius:16px;background:white;box-shadow:0 8px 24px #16452b10}.card{text-decoration:none;color:inherit}.card span{color:#52645b}.readme{margin:28px 0;padding:24px;background:white;border-radius:16px;overflow:auto}small{color:#687a70;font-weight:normal}"
    for relative in [Path("index.html")]:
        (SITE_DIR / relative).write_text(f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>MakoSU Modules</title><style>{style}</style><main id="app">Loading...</main><script>{page_script}</script>', encoding="utf-8")
    for detail in details:
        path = SITE_DIR / "module" / detail["moduleId"] / "index.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((SITE_DIR / "index.html").read_text(encoding="utf-8").replace("`module/${encodeURIComponent(id)}.json`", "`../../module/${encodeURIComponent(id)}.json`").replace("href=\"module/", "href=\"../../module/"), encoding="utf-8")


if __name__ == "__main__":
    main()
