"""tiesapi.com — TIES: the website and the receipt endpoint. Stores nothing. Python standard library only.

A browser (Accept: text/html) gets a page. Anything else (curl, an agent) gets JSON.

  GET  /              what TIES is; the calls; the catalog
  GET  /catalog       the catalog: the tie keywords (catalog/keywords.tsv) and every entry with its URL and sha256
  GET  /catalog/<e>   one entry: the exact bytes of catalog/entries/<e> and their sha256
  POST /receipt       {"federated_id", "line"}  -> {"receipt", "issued_at", "hash", "label", "label_sha256", "inside", ...}
                      line = one canonical register line (SOP-001): n, when, who, label (a catalog URL), inside (sha256 of
                      the content, which stays with the holder), parents, memo, by, prev, hash. Line 1 is the registration.
                      A v0 tie line (SPEC.md: frame "tie", field, value) is still accepted.
  POST /verify        {"federated_id", "hash", "issued_at", "receipt"} -> {"valid": true|false}
  GET  /robots.txt, /favicon.ico. HEAD answers like GET, without the body.

The catalog is read from catalog/ beside this file (the published bundle) or ../catalog (the repository).
receipt = HMAC-SHA256(TIES_KEY, federated_id | tie_hash | issued_at), hex.
Every JSON reply carries "status" in the body as well as the HTTP status.
"""
import hashlib, hmac, html, json, os, re
from datetime import datetime, timezone

AUTHORITY = "theisen-api"
CONTACT = "doctortheisen@gmail.com"
HOME = "https://tiesapi.com"
SOURCE = "https://github.com/tiesapi/ties"
FID = re.compile(r"^theisen-api:[0-9a-f]{16}$")
ADDR = re.compile(r"^(sha256:[0-9a-f]{64}|theisen-api:[0-9a-f]{16}|[a-z][a-z0-9+.-]*:\S+)$")
KEYWORD = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
SEGMENT = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*$")
TIE_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
ISSUED_AT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
ATTESTS = ("this line was presented at issued_at and hashes to hash; the federated id is as claimed by the presenter, "
           "not proven (v0 has no per-operator keys)")
ATTESTS_ENTRY = ("this entry line was presented at issued_at and hashes to hash; its label was in the catalog with label_sha256 "
                 "at that time; the content behind inside was not seen; the federated id is as claimed by the presenter, "
                 "not proven (v0 has no per-operator keys)")
ENTRY = {"n": 1, "when": "2026-10-27T04:30:00-0700", "who": "theisen-api:<16 hex>", "label": f"{HOME}/catalog/<entry>",
         "inside": "sha256:<64 hex of the exact bytes of your content>", "parents": ["sha256:<hash of an earlier line>"],
         "memo": "public memo", "by": "human | agent | both", "prev": None, "hash": "sha256:<64 hex of this line without hash>"}
ENTRY_TEXT = """n        line number, from 1
when     ISO 8601 with offset
who      the federated id of the holder (the constant)
label    a catalog URL: the abstract meaning, public
inside   sha256:<64 hex> of the exact bytes of the content, private
parents  list of sha256 of earlier entries this one ties to (empty for the first)
memo     public memo; outside the content, never changes the inside hash
by       human | agent | both
prev     hash of the line before (null on line 1)
hash     sha256 of the compact line without the hash field"""
MAX_BODY = 16 * 1024
HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = next((p for p in (os.environ.get("TIES_CATALOG", ""), os.path.join(HERE, "catalog"), os.path.join(HERE, "..", "catalog"))
                if p and os.path.isdir(p)), os.path.join(HERE, "catalog"))
WHAT = ("Records the ties between things, not the things. You keep your things and your chain of ties; "
        "each thing is named by its content address (sha256). A tie says: this connects to that, in this way, "
        "from this operator, at this time, with a public memo.")
CALLS = """GET  /                  what TIES is, in plain words
GET  /catalog           the catalog: every label, its URL and its sha256
GET  /catalog/<entry>   one label: the exact bytes and their sha256
POST /receipt           an entry line -> a timestamped receipt; stores nothing
POST /verify            a receipt -> valid or not"""
FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><rect width="16" height="16" rx="3" fill="#1a1a1a"/>'
           '<text x="8" y="12.5" font-family="monospace" font-size="12" font-weight="bold" fill="#fff" text-anchor="middle">T</text></svg>\n')
STYLE = """body{font-family:ui-monospace,Menlo,Consolas,monospace;max-width:640px;margin:0 auto;padding:16px;line-height:1.5;background:#fff;color:#222}
a{color:#06c}h1{font-size:20px;margin:0 0 8px}h2{font-size:16px;margin:24px 0 6px;border-bottom:1px solid #999;padding-bottom:4px}
pre{white-space:pre-wrap;word-break:break-word;background:#f4f4f4;padding:10px;font-size:13px}code{word-break:break-all}
.dim{color:#777;font-size:13px}ul{padding-left:18px}li{margin:6px 0}
@media (prefers-color-scheme:dark){body{background:#1a1a1a;color:#ddd}a{color:#6cf}pre{background:#111}.dim{color:#999}}"""


def _key():
    k = os.environ.get("TIES_KEY", "")
    if len(k) < 32:
        raise RuntimeError("TIES_KEY is not set")
    return k.encode()


# ---- the catalog --------------------------------------------------------------------------------------------------

def _keywords():
    path = os.path.join(CATALOG, "keywords.tsv")
    out = []
    if os.path.exists(path):
        for row in open(path, encoding="utf-8"):
            if row.strip() and not row.startswith("#"):
                kw, _, definition = row.rstrip("\n").partition("\t")
                out.append({"keyword": kw, "definition": definition})
    return out


def _entries():
    root = os.path.join(CATALOG, "entries")
    out = []
    for d, _, files in os.walk(root):
        for f in files:
            name = os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/")
            if all(SEGMENT.match(s) for s in name.split("/")):
                out.append(name)
    return sorted(out)


def _entry(name):
    """One catalog entry: the exact bytes as stored and their sha256. None if there is no such entry."""
    if not name or not all(SEGMENT.match(s) for s in name.split("/")):
        return None
    path = os.path.join(CATALOG, "entries", *name.split("/"))
    if not os.path.isfile(path):
        return None
    data = open(path, "rb").read()
    return dict(entry=name, url=f"{HOME}/catalog/{name}",
                sha256="sha256:" + hashlib.sha256(data).hexdigest(), bytes=data.decode("utf-8", "replace"))


def _listing():
    return [{k: e[k] for k in ("entry", "url", "sha256")} for e in map(_entry, _entries())]


# ---- replies ------------------------------------------------------------------------------------------------------

def _raw(status, ctype, text):
    return {"statusCode": status, "headers": {"Content-Type": ctype, "Access-Control-Allow-Origin": "*"}, "body": text}


def _reply(status, body):
    body = dict(status=status, **body)
    return _raw(status, "application/json", json.dumps(body, ensure_ascii=False, indent=2) + "\n")


def _page(status, title, body):
    h = html.escape
    doc = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<title>{h(title)}</title><link rel="icon" href="/favicon.ico" type="image/svg+xml"><style>{STYLE}</style></head><body>'
           f'<p class="dim"><a href="/">tiesapi.com</a> · <a href="/catalog">catalog</a> · <a href="{SOURCE}">source</a></p>'
           f'{body}'
           f'<p class="dim">Authority: {AUTHORITY}, Dr. Theisen · <a href="https://doctortheisen.com/">doctortheisen.com</a> · '
           f'Problem? <a href="mailto:{CONTACT}">{CONTACT}</a></p></body></html>\n')
    return _raw(status, "text/html; charset=utf-8", doc)


def _entry_items():
    h = html.escape
    return "".join(f'<li><a href="/catalog/{h(e["entry"])}">{h(e["entry"])}</a> <span class="dim">{h(e["sha256"])}</span></li>'
                   for e in _listing()) or '<li class="dim">none yet</li>'


def _keyword_items():
    h = html.escape
    return "".join(f'<li>{h(k["keyword"])} <span class="dim">{h(k["definition"])}</span></li>' for k in _keywords()) \
        or '<li class="dim">none yet</li>'


def _home_html():
    h = html.escape
    return _page(200, "TIES", (
        f"<h1>TIES</h1><p>{h(WHAT)}</p>"
        "<p>The catalog states the abstract meaning once, in plain words, at a fixed URL. The person keeps the concrete content. "
        "Anyone can read what was done and when from the labels, without seeing the content.</p>"
        f"<h2>the catalog</h2><ul>{_entry_items()}</ul>"
        f"<p class=\"dim\">keywords (the ties, in doctortheisen's words):</p><ul>{_keyword_items()}</ul>"
        f"<h2>the calls</h2><pre>{h(CALLS)}</pre>"
        "<p>A browser gets a page; curl gets JSON. Nothing is stored here.</p>"
        f"<h2>the entry line</h2><p>One file, append-only, one JSON line per entry. POST a line to /receipt; line 1 is the registration.</p><pre>{h(ENTRY_TEXT)}</pre>"
        "<h2>on your device</h2>"
        f"<p><code>bin/ties</code>: register, add, context, sop, export, verify, receipt. Source and spec: <a href=\"{SOURCE}\">{h(SOURCE)}</a>. "
        "Check it yourself: <code>./test.sh</code> runs one check per user story.</p>"
        "<h2>the first SOP</h2><p><a href=\"/catalog/sop-001\">SOP-001 · register, label, keep</a></p>"))


def _catalog_html():
    return _page(200, "TIES · catalog", (
        "<h1>the catalog</h1><p>Every label, its URL and the sha256 of its exact bytes. Fetch a URL with curl for JSON.</p>"
        f"<h2>entries</h2><ul>{_entry_items()}</ul>"
        f"<h2>keywords</h2><ul>{_keyword_items()}</ul>"))


def _entry_html(e):
    h = html.escape
    first = e["bytes"].split("\n", 1)[0]
    title = first[2:].strip() if first.startswith("# ") else e["entry"]
    return _page(200, f"TIES · {title}", (
        f"<h1>{h(title)}</h1><p class=\"dim\">{h(e['url'])}<br>{h(e['sha256'])}</p>"
        f"<pre>{h(e['bytes'])}</pre>"
        f"<p class=\"dim\">The hash is of these exact bytes, trailing newline included. <code>curl {h(e['url'])}</code> returns them with the sha256 as JSON.</p>"))


def _not_found_html(path):
    return _page(404, "TIES · not found", f"<h1>not found</h1><p class=\"dim\">{html.escape(path)}</p><p><a href=\"/\">tiesapi.com</a></p>")


# ---- receipts -----------------------------------------------------------------------------------------------------

def _sign(fid, tie_hash, issued_at):
    # every field is format-checked before signing (FID, TIE_HASH, ISSUED_AT), so none can contain the separator
    return hmac.new(_key(), f"ties-receipt-v0|{fid}|{tie_hash}|{issued_at}".encode(), hashlib.sha256).hexdigest()


def _check_line(fid, line):
    """The line must be JSON, hash to its own hash, and come from this federated id. Returns (hash, extra, error):
    extra is {label, label_sha256, inside} for a canonical entry line, {} for a v0 tie line."""
    try:
        d = json.loads(line) if isinstance(line, str) else dict(line)
    except (ValueError, TypeError):
        return None, None, "line is not JSON"
    h = d.pop("hash", None)
    body = json.dumps(d, separators=(",", ":"), ensure_ascii=False)
    if h != "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest():
        return None, None, "line hash does not recompute"
    if d.get("who") != fid:
        return None, None, "not a line from this federated id"
    if "label" in d:
        extra, err = _check_entry(d)
        return (h, extra, None) if not err else (None, None, err)
    err = _check_tie(d)
    return (h, {}, None) if not err else (None, None, err)


def _check_entry(d):
    """A canonical register line (SOP-001). The label must be in the catalog; the content stays with the holder."""
    label = d.get("label")
    prefix = f"{HOME}/catalog/"
    if not (isinstance(label, str) and label.startswith(prefix)):
        return None, f"label must be a catalog URL: {prefix}<entry>"
    e = _entry(label[len(prefix):].rstrip("/"))
    if not e:
        return None, "label is not in the catalog"
    if not TIE_HASH.match(d.get("inside") or ""):
        return None, "inside must be sha256:<64 hex> of your content"
    parents = d.get("parents")
    if not isinstance(parents, list) or not all(isinstance(x, str) and TIE_HASH.match(x) for x in parents):
        return None, "parents must be a list of sha256:<64 hex>"
    if d.get("by") not in ("human", "agent", "both"):
        return None, "by must be human, agent or both"
    if not isinstance(d.get("memo", ""), str):
        return None, "memo must be text"
    n, prev = d.get("n"), d.get("prev")
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        return None, "n must be the line number, from 1"
    if n == 1 and (prev is not None or label != f"{prefix}registration"):
        return None, "line 1 is the registration: label .../catalog/registration, prev null"
    if n > 1 and not (isinstance(prev, str) and TIE_HASH.match(prev)):
        return None, "prev must be the hash of the line before"
    return dict(label=label, label_sha256=e["sha256"], inside=d["inside"]), None


def _check_tie(d):
    """A v0 tie line (SPEC.md)."""
    if d.get("frame") != "tie":
        return "not a tie line (frame) and not an entry line (label)"
    if not KEYWORD.match(d.get("field") or ""):
        return "keyword is not a word"
    try:
        v = json.loads(d.get("value") or "")
    except ValueError:
        return "value is not JSON"
    if not (ADDR.match(v.get("from") or "") and ADDR.match(v.get("to") or "")):
        return "from/to are not addresses"
    if v.get("visibility") not in ("private", "public"):
        return "visibility must be private or public"
    return None


# ---- the handler --------------------------------------------------------------------------------------------------

def lambda_handler(event, context=None):
    method = (event.get("requestContext", {}).get("http", {}).get("method") or event.get("httpMethod") or "GET").upper()
    path = event.get("rawPath") or event.get("path") or "/"
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    browser = "text/html" in (headers.get("accept") or "")
    head = method == "HEAD"
    if head:
        method = "GET"
    r = _route(method, path, browser, event)
    if head:
        r = dict(r, body="")
    return r


def _route(method, path, browser, event):
    if method == "GET" and path in ("/", ""):
        if browser:
            return _home_html()
        return _reply(200, dict(name="TIES API", authority=AUTHORITY, contact=CONTACT, what=WHAT, spec=f"{SOURCE}/blob/main/SPEC.md",
                                sop=f"{HOME}/catalog/sop-001", source=SOURCE,
                                routes=["GET /catalog", "GET /catalog/<entry>", "POST /receipt", "POST /verify"],
                                receipt={"federated_id": "theisen-api:<16 hex>", "line": ENTRY}))
    if method == "GET" and path == "/catalog":
        return _catalog_html() if browser else _reply(200, dict(authority=AUTHORITY, keywords=_keywords(), entries=_listing()))
    if method == "GET" and path.startswith("/catalog/"):
        e = _entry(path[len("/catalog/"):].rstrip("/"))
        if e:
            return _entry_html(e) if browser else _reply(200, dict(authority=AUTHORITY, **e))
        return _not_found_html(path) if browser else _reply(404, dict(error="no such catalog entry"))
    if method == "GET" and path == "/robots.txt":
        return _raw(200, "text/plain; charset=utf-8", "User-agent: *\nAllow: /\n")
    if method == "GET" and path == "/favicon.ico":
        return _raw(200, "image/svg+xml", FAVICON)
    if method != "POST" or path not in ("/receipt", "/verify"):
        return _not_found_html(path) if browser else _reply(404, dict(error="no such route"))
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode("utf-8", "replace")
    if len(raw) > MAX_BODY:
        return _reply(413, dict(error="body too large"))
    try:
        req = json.loads(raw)
        assert isinstance(req, dict)
    except Exception:
        return _reply(400, dict(error="send a JSON object"))
    fid = req.get("federated_id") or ""
    if not FID.match(fid):
        return _reply(400, dict(error="federated_id must look like theisen-api:<16 hex>"))
    if path == "/receipt":
        h, extra, err = _check_line(fid, req.get("line"))
        if err:
            return _reply(400, dict(error=err, entry=ENTRY))
        issued_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return _reply(201, dict(receipt=_sign(fid, h, issued_at), issued_at=issued_at, hash=h, **extra, federated_id=fid,
                                authority=AUTHORITY, stored=False, attests=ATTESTS_ENTRY if extra else ATTESTS, contact=CONTACT))
    h = req.get("hash") or req.get("tie_hash") or ""
    issued_at, receipt = req.get("issued_at") or "", req.get("receipt") or ""
    if not (TIE_HASH.match(h) and ISSUED_AT.match(issued_at) and re.fullmatch(r"[0-9a-f]{64}", receipt)):
        return _reply(400, dict(error="hash, issued_at or receipt is not in the expected form"))
    valid = hmac.compare_digest(_sign(fid, h, issued_at), receipt)
    return _reply(200, dict(valid=valid, federated_id=fid, hash=h, issued_at=issued_at, attests=ATTESTS))
