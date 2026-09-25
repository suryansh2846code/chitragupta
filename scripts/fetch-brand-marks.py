"""Pull brand marks once, at development time, and print them as JS.

The page must never fetch these: an asset pulled from a vendor's CDN is a
request that tells them which app the user is running, which is the promise
`connectors.js` is built on. So the bytes are fetched here and committed.

simple-icons is CC0; the trademarks stay their owners', and naming a service
you connect to is exactly what they are for.
"""
import json
import sys
import urllib.request

VERSION = "13.21.0"
BASE = f"https://cdn.jsdelivr.net/npm/simple-icons@{VERSION}"

WANT = {
    "linear": "linear", "asana": "asana", "atlassian": "atlassian",
    "todoist": "todoist", "clickup": "clickup", "zapier": "zapier",
    "notion": "notion", "airtable": "airtable", "figma": "figma",
    "canva": "canva", "webflow": "webflow", "github": "github",
    "sentry": "sentry", "vercel": "vercel", "cloudflare": "cloudflare",
    "datadog": "datadog", "stripe": "stripe", "paypal": "paypal",
    "square": "square", "intercom": "intercom", "slack": "slack",
    "telegram": "telegram", "google_fit": "googlefit", "neon": "neon",
    "fireflies": "fireflies", "deepwiki": "deepwiki", "context7": "context7",
    "apple_health": "apple",
}

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "chitragupta-dev"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode()

data = json.loads(get(f"{BASE}/_data/simple-icons.json"))
icons = data["icons"] if isinstance(data, dict) else data
def slugify(t):
    return t.lower().replace(" ", "").replace(".", "").replace("-", "")
hexes = {}
for i in icons:
    hexes[i.get("slug") or slugify(i["title"])] = i["hex"]

out, missing = {}, []
for ours, slug in WANT.items():
    try:
        svg = get(f"{BASE}/icons/{slug}.svg")
    except Exception:
        missing.append(ours); continue
    start = svg.index('d="') + 3
    path = svg[start:svg.index('"', start)]
    out[ours] = (path, hexes.get(slug, ""))

# Printed as the JS object body, ready to paste into `BRAND_MARKS` in
# `chitragupta/web/connectors.js`. Deliberately not written into the file
# automatically: a script that edits the frontend is a build step, and this
# project does not have one.
for key in sorted(out):
    path, hexv = out[key]
    print(f'  {key}: ["{path}", "#{hexv}"],')
print(f"// {len(out)} marks from simple-icons {VERSION}; "
      f"no mark published for: {', '.join(missing) or 'none'}", file=sys.stderr)
