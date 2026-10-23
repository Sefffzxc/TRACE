"""Read installed/pinned versions; query public PyPI metadata, no installs.

Uses stdlib only. Network destinations are pypi.org version metadata URLs.
Writes sanitized version/advisory identifiers to audit/python-dependencies.json.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def normalized(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def snapshot(executable):
    raw = subprocess.check_output([str(ROOT / executable), "-m", "pip", "list", "--format=json", "--disable-pip-version-check"], text=True)
    return {normalized(row["name"]): row["version"] for row in json.loads(raw)}


def pins(filename):
    rows = {}
    for line in (ROOT / filename).read_text(encoding="utf-8").splitlines():
        match = re.match(r"([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?==([^\s#]+)", line)
        if match:
            rows[normalized(match[1])] = match[2]
    return rows


environments = {
    "backend_installed": snapshot("backend/.venv/Scripts/python.exe"),
    "ml_probe_environment_installed": snapshot("ml/.venv-ml/Scripts/python.exe"),
    "backend_requirements_pinned": pins("backend/requirements.txt"),
    "ai_requirements_pinned": pins("ai-service/requirements.txt"),
}
pairs = sorted({(name, version) for rows in environments.values() for name, version in rows.items()})


def check(pair):
    name, version = pair
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    try:
        with urlopen(Request(url, headers={"User-Agent": "TRACE-launch-audit/1"}), timeout=25) as response:
            metadata = json.load(response)
        return {
            "package": name, "version": version, "source_url": url,
            "environments": [env for env, rows in environments.items() if rows.get(name) == version],
            "requires_dist": metadata.get("info", {}).get("requires_dist"),
            "advisories": [{k: issue.get(k) for k in ("id", "aliases", "fixed_in", "link", "source", "withdrawn")} for issue in metadata.get("vulnerabilities", [])],
        }
    except Exception as exc:
        return {"package": name, "version": version, "source_url": url, "error": str(exc)}


with ThreadPoolExecutor(max_workers=6) as pool:
    checks = list(pool.map(check, pairs))
evidence = {"checked_utc": datetime.now(timezone.utc).isoformat(), "method": "Read-only installed/pinned version snapshot + PyPI per-version vulnerability metadata; no installation/resolution or exploit tests.", "environments": environments, "checks": checks}
(ROOT / "audit" / "python-dependencies.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
summary = {"unique_package_versions_checked": len(checks), "errors": [{k: row[k] for k in ("package", "version", "error")} for row in checks if "error" in row], "affected": [{"package": row["package"], "version": row["version"], "environments": row["environments"], "advisories": row["advisories"]} for row in checks if row.get("advisories")]}
print(json.dumps(summary, indent=2))
