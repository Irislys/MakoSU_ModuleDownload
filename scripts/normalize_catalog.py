#!/usr/bin/env python3
"""Convert the maintained catalog to the KernelSU catalog/detail protocol."""

from __future__ import annotations

import base64
import html
import json
import os
import re
import sys
import time
from urllib.parse import quote, unquote, urljoin, urlparse
from urllib.request import Request, urlopen
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES_FILE = ROOT / "modules.json"
DETAIL_DIR = ROOT / "module"
SITE_DIR = ROOT / "site"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_API = "https://api.github.com"


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


def github_request(path: str, method: str = "GET", payload: dict | None = None) -> bytes | None:
    body = None
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "MakoSU-ModuleProxy/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(
        f"{GITHUB_API}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    if GITHUB_TOKEN:
        request.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                return response.read()
        except Exception as error:  # noqa: BLE001
            if attempt == 2:
                print(f"GitHub API unavailable for {path}: {error}", file=sys.stderr)
            else:
                time.sleep(2 ** attempt)
    return None


def github_api(path: str) -> dict | list | None:
    response = github_request(path)
    if response is None:
        return None
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        return None


def github_markdown(markdown: str, context: str | None) -> str | None:
    if not markdown.strip():
        return ""
    payload = {"text": markdown, "mode": "gfm"}
    if context:
        payload["context"] = context
    response = github_request("/markdown", method="POST", payload=payload)
    if response is None:
        return None
    return response.decode("utf-8")


def repo_name(repo_url: str) -> str | None:
    parsed = urlparse(repo_url)
    if parsed.netloc.lower() != "github.com":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        return None
    return f"{parts[0]}/{parts[1].removesuffix('.git')}"


def inline_markdown(value: str) -> str:
    escaped = html.escape(value, quote=True)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"__([^_]+)__", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)
    escaped = re.sub(r"_([^_]+)_", r"<em>\1</em>", escaped)
    return re.sub(r"\[([^]]+)\]\((https?://[^ )]+)\)", r'<a href="\2">\1</a>', escaped)


def markdown_to_html(markdown: str) -> str:
    lines = (markdown or "").replace("\r\n", "\n").split("\n")
    output: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    code_lines: list[str] = []
    in_code = False

    def flush_paragraph() -> None:
        if paragraph:
            output.append(f"<p>{'<br>\n'.join(paragraph)}</p>")
            paragraph.clear()

    def flush_list() -> None:
        if list_items:
            output.append(f"<ul>{''.join(f'<li>{item}</li>' for item in list_items)}</ul>")
            list_items.clear()

    for line in lines:
        if line.strip().startswith("```"):
            flush_paragraph()
            flush_list()
            if in_code:
                output.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
                code_lines.clear()
            in_code = not in_code
            continue
        if in_code:
            code_lines.append(line)
            continue
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            flush_list()
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            flush_list()
            level = len(heading.group(1))
            output.append(f"<h{level}>{inline_markdown(heading.group(2))}</h{level}>")
            continue
        if re.match(r"^[-*_]{3,}$", stripped):
            flush_paragraph()
            flush_list()
            output.append("<hr>")
            continue
        bullet = re.match(r"^[-*+]\s+(.+)$", stripped)
        if bullet:
            flush_paragraph()
            list_items.append(inline_markdown(bullet.group(1)))
            continue
        if list_items:
            flush_list()
        quote_line = re.match(r"^>\s?(.*)$", stripped)
        if quote_line:
            flush_paragraph()
            output.append(f"<blockquote><p>{inline_markdown(quote_line.group(1))}</p></blockquote>")
            continue
        paragraph.append(inline_markdown(stripped))

    if in_code:
        output.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
    flush_paragraph()
    flush_list()
    return "\n".join(output)


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


def fallback_readme(module: dict) -> tuple[str, str]:
    markdown = f"# {module.get('moduleName', '')}\n\n{module.get('summary', '')}\n"
    return markdown, markdown_to_html(markdown)


def absolutize_readme_links(rendered: str, readme: dict) -> str:
    raw_base = str(readme.get("download_url") or "")
    html_base = str(readme.get("html_url") or "")
    if not raw_base and not html_base:
        return rendered

    def replace_src(match: re.Match[str]) -> str:
        return f'{match.group(1)}{urljoin(raw_base, match.group(2))}{match.group(3)}'

    def replace_href(match: re.Match[str]) -> str:
        value = match.group(2)
        if value.startswith(("#", "data:", "http:", "https:", "mailto:")):
            return match.group(0)
        return f'{match.group(1)}{urljoin(html_base, value)}{match.group(3)}'

    rendered = re.sub(r'(<img[^>]+\ssrc=")([^"]+)(")', replace_src, rendered, flags=re.IGNORECASE)
    return re.sub(r'(<a[^>]+\shref=")([^"]+)(")', replace_href, rendered, flags=re.IGNORECASE)


def render_readme(module: dict, full_repo: str | None) -> tuple[str, str]:
    if full_repo:
        for path in ("README.md", "docs/README.md", "docs/README_en.md"):
            readme = github_api(f"/repos/{full_repo}/contents/{quote(path, safe='/')}")
            if not readme or not readme.get("content"):
                continue
            try:
                markdown = base64.b64decode(readme["content"]).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                continue
            rendered = github_markdown(markdown, full_repo) or markdown_to_html(markdown)
            rendered = absolutize_readme_links(rendered, readme)
            return markdown, rendered
    return fallback_readme(module)


def asset_from_github(asset: dict) -> dict:
    return {
        "name": str(asset.get("name", "")),
        "contentType": str(asset.get("content_type", "application/octet-stream")),
        "downloadUrl": direct_url(str(asset.get("browser_download_url", ""))),
        "downloadCount": int(asset.get("download_count", 0) or 0),
        "size": int(asset.get("size", 0) or 0),
    }


def release_from_github(full_repo: str | None, tag: str, fallback: dict, summary: str) -> dict:
    if not full_repo:
        return fallback
    encoded_tag = quote(tag, safe="")
    remote = github_api(f"/repos/{full_repo}/releases/tags/{encoded_tag}")
    if not remote:
        return fallback
    assets = [asset_from_github(asset) for asset in remote.get("assets", [])]
    assets = [asset for asset in assets if asset["name"] and asset["downloadUrl"]]
    body = str(remote.get("body") or "").strip()
    rendered_body = github_markdown(body, full_repo) if body else None
    return {
        "name": fallback["name"],
        "url": str(remote.get("html_url") or fallback["url"]),
        "descriptionHTML": rendered_body or (markdown_to_html(body) if body else f"<p>{html.escape(summary)}</p>"),
        "createdAt": iso_date(str(remote.get("created_at") or fallback["createdAt"])),
        "publishedAt": iso_date(str(remote.get("published_at") or fallback["publishedAt"])),
        "updatedAt": iso_date(str(remote.get("updated_at") or fallback["updatedAt"])),
        "tagName": str(remote.get("tag_name") or fallback["tagName"]),
        "isPrerelease": bool(remote.get("prerelease", fallback["isPrerelease"])),
        "releaseAssets": assets or fallback["releaseAssets"],
        "version": fallback["version"],
        "versionCode": fallback["versionCode"],
    }


def normalize_module(module: dict) -> tuple[dict, dict]:
    module_id = str(module.get("moduleId", "")).strip()
    if not module_id:
        raise ValueError("moduleId is required")
    release = dict(module.get("latestRelease") or {})
    name = str(release.get("name") or release.get("version") or module_id)
    release_time = iso_date(str(release.get("time", "")))
    repo_url = str(module.get("repoUrl", "")).strip()
    full_repo = repo_name(repo_url)
    readme, readme_html = render_readme(module, full_repo)
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
    detail_release = release_from_github(full_repo, tag, detail_release, str(module.get("summary", "")))
    latest_asset = next(
        (item for item in detail_release["releaseAssets"] if item["name"] == asset["name"]),
        detail_release["releaseAssets"][0] if detail_release["releaseAssets"] else asset,
    )

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
            "time": detail_release["publishedAt"],
            "version": name,
            "versionCode": version_code(name),
            "downloadUrl": latest_asset["downloadUrl"],
            "downloadCount": latest_asset["downloadCount"],
            "size": latest_asset["size"],
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
    page_script = r"""
const root = document.querySelector('#app');
const id = location.pathname.match(/module\/([^/]+)\/?$/)?.[1];
const esc = value => String(value ?? '').replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));
async function main() {
  const response = await fetch(id ? `module/${encodeURIComponent(id)}.json` : 'modules.json');
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const data = await response.json();
  if (id) {
    root.innerHTML = `<p><a href="../../">&larr; All modules</a></p><h1>${esc(data.moduleName)}</h1><p>${esc(data.summary)}</p><nav><a href="${esc(data.homepageUrl)}">Homepage</a> <a href="${esc(data.sourceUrl)}">Source</a></nav><section class="readme">${data.readmeHTML || '<p>No README provided.</p>'}</section><h2>Releases</h2>${(data.releases || []).map(r => `<article><h3>${esc(r.name)} <small>${esc(r.tagName)}</small></h3><p>${esc(r.publishedAt || '')}</p>${r.descriptionHTML || ''}<ul>${(r.releaseAssets || []).map(a => `<li><a href="${esc(a.downloadUrl)}">${esc(a.name)}</a> <small>${Number(a.size || 0).toLocaleString()} bytes, ${Number(a.downloadCount || 0).toLocaleString()} downloads</small></li>`).join('')}</ul></article>`).join('')}`;
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
