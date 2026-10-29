"""tiesapi.com — TIES: the website and the receipt endpoint. Stores nothing. Python standard library only.

A browser (Accept: text/html) gets a page. Anything else (curl, an agent) gets JSON.

  GET  /              what TIES is; the calls; the catalog
  GET  /catalog       the catalog: the tie keywords (catalog/keywords.tsv) and every entry with its URL and sha256
  GET  /catalog/<e>   one entry: the exact bytes of catalog/entries/<e> and their sha256
  POST /receipt       {"federated_id", "line"}  -> {"receipt", "issued_at", "hash", "label", "label_sha256", "inside", ...}
                      line = one canonical register line (SOP-001): n, when, who, label (a catalog URL), inside (sha256 of
                      the content, which stays with the holder), parents, memo, by, prev, hash. Line 1 is the registration.
                      A v0 tie line (SPEC.md: frame "tie", field, value) is still accepted.
  POST /verify        {"federated_id", "hash", "issued_at", "receipt", "label_sha256" (entry receipts)} -> {"valid": true|false}
  POST /sop/check     {"federated_id", "sop", "lines", "receipts"} -> {"valid", "complete", "steps", "done", "timed", "error"}
  GET  /docs          the API reference (DOCS below), byte-exact with its sha256
  GET  /api           the contracts: one page per route, in one form (catalog/contracts/<name>, one file each)
  GET  /api/<c>       one contract: input, processing, output, graph (its base-then-append chain), an example run on the view
  GET  /robots.txt, /favicon.ico. HEAD answers like GET, without the body.

The catalog is read from catalog/ beside this file (the published bundle) or ../catalog (the repository).
The contracts are read from catalog/contracts/ (or TIES_CONTRACTS); bin/publish does not bundle them today.
receipt = HMAC-SHA256(TIES_KEY, "ties-receipt-v1" | federated_id | hash | issued_at | label_sha256), hex, for an entry line
(hash already covers label and inside); "ties-receipt-v0" | federated_id | hash | issued_at for a v0 tie line.
Every JSON reply carries "status" in the body as well as the HTTP status. Every reply carries X-Request-Id, and every request
writes one JSON line to stdout (CloudWatch Logs on Lambda) with no request content in it: see Logging in DOCS.
"""
import hashlib, hmac, html, json, os, re, time, traceback, uuid
from datetime import datetime, timezone

AUTHORITY = "theisen-api"
CONTACT = "doctortheisen@gmail.com"
HOME = "https://tiesapi.com"
SOURCE = "https://github.com/tiesapi/ties"
FID = re.compile(r"^theisen-api:[0-9a-f]{16}\Z")
ADDR = re.compile(r"^(sha256:[0-9a-f]{64}|theisen-api:[0-9a-f]{16}|[a-z][a-z0-9+.-]*:\S+)$")
KEYWORD = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
SEGMENT = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*$")
TIE_HASH = re.compile(r"^sha256:[0-9a-f]{64}\Z")
ISSUED_AT = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
ATTESTS = ("this line was presented at issued_at and hashes to hash; the federated id is as claimed by the presenter, "
           "not proven (v0 has no per-operator keys)")
ATTESTS_ENTRY = ("this entry line was presented at issued_at and hashes to hash; its label was in the catalog with label_sha256 "
                 "at that time; the content behind inside was not seen; the federated id is as claimed by the presenter, "
                 "not proven (v0 has no per-operator keys)")
ATTESTS_ADDRESS = ("this entry line was presented at issued_at and hashes to hash; its label is a content address, valid as an id "
                   "before any name is assigned, so nothing was looked up in the catalog; the content behind inside was not seen; "
                   "a receipt attests one line, not a whole SOP run (POST /sop/check); the federated id is as claimed by the "
                   "presenter, not proven (v0 has no per-operator keys)")
ATTESTS_LABEL = ("this entry line was presented at issued_at and hashes to hash, with label_sha256: for a named label, the sha256 "
                 "of its catalog bytes at that time; for a content-address label, the address itself, nothing looked up; the "
                 "federated id is as claimed by the presenter, not proven (v0 has no per-operator keys)")
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
MAX_SOP_BODY = 64 * 1024                                                         # /sop/check carries a whole run
HERE = os.path.dirname(os.path.abspath(__file__))
CODE = "sha256:" + hashlib.sha256(open(os.path.abspath(__file__), "rb").read()).hexdigest()   # the running code, for GET /
CATALOG = next((p for p in (os.environ.get("TIES_CATALOG", ""), os.path.join(HERE, "catalog"), os.path.join(HERE, "..", "catalog"))
                if p and os.path.isdir(p)), os.path.join(HERE, "catalog"))
OBJECTS = os.environ.get("TIES_OBJECTS") or os.path.join(CATALOG, "objects")   # objects/<64 hex>: bytes named by their sha256
CONTRACTS = os.environ.get("TIES_CONTRACTS") or os.path.join(CATALOG, "contracts")   # contracts/<name>: one file per route
HEX = re.compile(r"^[0-9a-f]{64}\Z")
PARTS = ("input", "processing", "output", "graph")                              # the parts of a page (names: his word)
STEP = re.compile(r"[ \t]*([0-9]{1,6})[ \t]+(?:https://tiesapi\.com/catalog/)?sha256:([0-9a-f]{64})[ \t]*")
WHAT = ("Records the ties between things, not the things. You keep your things and your chain of ties; "
        "each thing is named by its content address (sha256). A tie says: this connects to that, in this way, "
        "from this operator, at this time, with a public memo.")
CALLS = """GET  /                  what TIES is, in plain words
GET  /catalog           the catalog: every label, its URL and its sha256
GET  /catalog/<entry>   one label: the exact bytes and their sha256
GET  /catalog/sha256:<hex>
                        one object by its content address: the id exists before any name is assigned
POST /receipt           an entry line -> a timestamped receipt; stores nothing
POST /verify            a receipt -> valid or not
POST /sop/check         an SOP and the lines of one run -> every step in order, or the first step that fails
GET  /docs              the API reference: every call, its fields and errors, and what is logged
GET  /api               the contracts: one page per route, in one form
GET  /api/<contract>    one contract: input, processing, output, graph, and a worked example"""
ROUTES = ["GET /docs", "GET /catalog", "GET /catalog/<entry>", "GET /catalog/sha256:<hex>", "POST /receipt", "POST /verify",
          "POST /sop/check", "GET /api", "GET /api/<contract>"]
DOCS = r"""# TIES API reference

https://tiesapi.com · this text is served byte-exact at https://tiesapi.com/docs with its sha256 · source https://github.com/tiesapi/ties
Authority: theisen-api, Dr. Theisen · Problem? doctortheisen@gmail.com, with the X-Request-Id of the reply

## Basics
- HTTPS only. https://www.tiesapi.com answers the same.
- A browser (Accept contains text/html) gets an HTML page; anything else gets JSON.
- Every JSON reply has "status", equal to the HTTP status. A refusal has "error": the reason, in words.
- Every reply has the header X-Request-Id. The same id is on the reply's log line (see Logging).
- Stores nothing. A request is checked and answered; nothing in it is kept, and the log line holds no request content.
- Body: one JSON object, at most 16,384 characters (65,536 for POST /sop/check).
- The gateway allows 10 requests a second, bursts of 20; over that the gateway answers 429 itself, without "status".
- CORS: Access-Control-Allow-Origin: *. HEAD answers like GET, without the body.
- Status: 200 ok · 201 receipt issued · 400 refused · 404 not found · 413 body too large · 500 internal error (with request_id).

## Routes
GET  /                        the routes, the docs URL, and code_sha256: the sha256 of the running lambda_function.py
GET  /docs                    this reference: {url, sha256, bytes}
GET  /catalog                 {keywords, entries: [{entry, url, sha256, title, parts}], objects: [{id, url, title, parts}]}
GET  /catalog/<entry>         one named entry: {entry, url, sha256, bytes}; 404 if there is none
GET  /catalog/sha256:<hex>    one object by content address: {id, url, sha256, bytes, title, parts}; 404 if no bytes are
                              here (the address is still a valid label)
POST /receipt                 {federated_id, line} -> 201 {receipt, issued_at, hash, label, label_sha256, inside, ...}
POST /verify                  {federated_id, hash, issued_at, receipt, label_sha256} -> 200 {valid}
POST /sop/check               {federated_id, sop, lines, receipts} -> 200 {valid, complete, steps, done, timed, error}
GET  /api                     the contracts, one page per route: {form, handlers, contracts: [{name, url, route, title, head,
                              current}]}
GET  /api/<contract>          one contract page: {route, input, processing, output, graph, example, ...}; 404 if there is none
GET  /robots.txt, GET /favicon.ico

## The register line (SOP-001, https://tiesapi.com/catalog/sop-001)
One JSON object per line, in a file that only grows. The fields, in the order bin/ties writes them:
  n        line number, from 1
  when     ISO 8601 time with offset (not checked by the endpoint)
  who      the federated id, theisen-api:<16 hex>; must equal the federated_id of the request
  label    https://tiesapi.com/catalog/<entry> (must be in the catalog), or
           https://tiesapi.com/catalog/sha256:<64 hex> (a content address: an id before any name; nothing is looked up)
  inside   sha256:<64 hex> of the exact bytes of the content; the content stays with the holder
  parents  list of sha256:<64 hex>: the hashes of earlier lines this one ties to ([] for none)
  memo     public text (may be left out)
  by       human | agent | both
  prev     the hash of the line before; null on line 1
  hash     sha256:<64 hex> of the line without hash, as compact JSON: keys in the order written, separators "," and ":",
           UTF-8, non-ASCII characters as they are (no \u escapes)
Line 1 is the registration: label .../catalog/registration or .../catalog/<its own inside>, prev null.
Other fields are covered by the hash and not checked, except the run fields below.
bin/ties derives the federated id from line 1: "theisen-api:" + the first 16 hex of sha256("theisen-api|" + the inside of
line 1). The endpoint checks only its form, and that who equals the federated_id sent.
A v0 tie line (SPEC.md: frame "tie", field, value) is also accepted.

Hash a line yourself:
  python3 -c 'import hashlib,json,sys; d=json.loads(sys.argv[1]); d.pop("hash",None); print("sha256:"+hashlib.sha256(json.dumps(d,separators=(",",":"),ensure_ascii=False).encode()).hexdigest())' '<line>'

## A run of an SOP (https://tiesapi.com/catalog/sop-nonce-chain)
The SOP is a text. Its steps are the lines "<k> sha256:<64 hex>" (the prefix https://tiesapi.com/catalog/ is allowed), k = 1..N
in order. Other lines are free text, but a line that starts with a digit and is not a step is refused.
The SOP's id is sha256:<hex> of its exact UTF-8 bytes.
A run is N+1 register lines, each with four more fields:
  sop         the SOP's id
  step        0 opens the run, then 1..N
  nonce       64 hex
  prev_nonce  64 hex: the nonce of the step before (steps 1..N)
Step 0: label https://tiesapi.com/catalog/<SOP id>.
Step k: label https://tiesapi.com/catalog/<id of step k>; parents include the hash of step k-1; prev_nonce = the nonce of
step k-1; sha256 of the 32 bytes of nonce = prev_nonce.
Make the nonces: pick a secret seed of 64 hex; the nonce of step N = seed; the nonce of step k-1 = sha256(nonce of step k).
Only the holder of the seed can show the next step, so a skipped, repeated or reordered step fails.
POST /receipt checks each run line alone (the nonce rule included). POST /sop/check checks the whole run.
A line with any of sop, step, nonce or prev_nonce is a run line, held to every run rule.

## POST /receipt
Request:   {"federated_id": "theisen-api:<16 hex>", "line": "<one register line, as a JSON string>"}
Reply 201: receipt, issued_at (UTC, whole seconds), hash, label, label_sha256, inside, federated_id, authority,
           stored (false), attests (what the receipt proves, in words), contact
label_sha256: for a named label, the sha256 of its catalog bytes at that time; for a content-address label, the address.
receipt = hex HMAC-SHA256(authority key, "ties-receipt-v1|<federated_id>|<hash>|<issued_at>|<label_sha256>")
          (a v0 tie line: "ties-receipt-v0|<federated_id>|<hash>|<issued_at>", no label fields)
A receipt proves that the line was presented at issued_at and hashes to hash. It does not prove who: the federated id is
as claimed (v0 has no per-operator keys).
Refusal: 400 with "error" and an "entry" template. For example: line hash does not recompute · not a line from this
federated id · label is not in the catalog · line 1 is the registration: ... · step k: nonce does not follow the step
before (skipped, repeated or out of order) · line has text that is not valid unicode (a lone surrogate).

## POST /verify
Request:   {"federated_id", "hash", "issued_at", "receipt", "label_sha256"}  (label_sha256 for an entry receipt; leave it out
           for a v0 receipt)
Reply 200: {valid: true | false, federated_id, hash, issued_at, attests}, and label_sha256 when it was sent. A malformed
field is 400.

## POST /sop/check
Request:   {"federated_id", "sop": "<the SOP text>", "lines": ["<step 0 line>", "<step 1 line>", ...],
            "receipts": [{"issued_at", "receipt"}, ...]}     receipts: optional, one per line, from POST /receipt
Reply 200: valid; error (the first failure, in words) when valid is false. Once the SOP text is read, also sop (its id),
           steps (N), done (the highest step that passed; 0 also when none did), timed (receipts were given) and
           complete (absent when a line is malformed).
valid is true only when steps 0..N are all there, in order, each bound to the one before; with receipts, each receipt
verifies and issued_at never goes backwards. issued_at counts whole seconds, so a run submitted within one second passes.
The reply is 200 even when valid is false; 400 only for a missing or malformed federated_id or body.

## Contracts: one page per route (https://tiesapi.com/api)
GET / and every route it lists has a contract: one file, catalog/contracts/<name>, in one form: input, processing, output,
graph, example, versions. https://tiesapi.com/api/<name> shows it: the parts, the example requests with the replies the
code gives on that view, and the graph: the base (sha256 of the page URL and the memo; no content yet), then one version
per line under versions (sha256 of the content, the memo, the hash of the link before), append only, and the contracts it
builds on and is built on by, each by URL and head hash. GET /api gives the form in full and the handlers a contract can name.
Add a contract: write one file; the router reads it. A route that is built in answers even when no contract file is here.
The pages are served only where catalog/contracts/ is in the bundle; where it is not, GET /api lists no contracts and
GET /api/<name> is 404, while every built-in route still answers.

## Examples
  curl https://tiesapi.com/
  curl -s https://tiesapi.com/docs | jq -r .bytes
  curl https://tiesapi.com/catalog
  curl -s -X POST https://tiesapi.com/receipt -H 'Content-Type: application/json' \
       -d "$(jq -cn --arg f "$FID" --arg l "$LINE" '{federated_id:$f, line:$l}')"
On a device: bin/ties enter <label> <inside> writes a line; bin/ties receipt <hash> gets its receipt; bin/ties verify
checks the chain.

## Logging
Where: CloudWatch Logs, log group /aws/lambda/ties-api, region us-east-1. Retention: 365 days (set 2026-10-28); CloudWatch
deletes older lines.
One JSON line per request:
  log         "ties-request"
  request_id  the X-Request-Id of the reply
  method      GET, HEAD or POST; any other method as "other"
  route       the route matched, for example "POST /receipt", or "none"
  status      the HTTP status sent
  ms          time in the handler, in milliseconds
  in_bytes    size of the request body as received (base64 when the gateway encoded it)
  out_bytes   size of the reply body
  browser     true when the caller asked for HTML
  code        the first 12 hex of code_sha256
  valid       POST /verify and POST /sop/check only: the answer
  error_type, where   a 500 only: the type of the error and the code lines it passed (file:line:function), never its message
Never logged: the URL path (only its route), the request body, lines, memos, hashes, federated ids, receipts, SOP texts,
the authority key, IP addresses, user agents.
Lambda adds its own START, END and REPORT lines (duration, memory) for each request.
Read it:
  aws logs filter-log-events --region us-east-1 --log-group-name /aws/lambda/ties-api \
      --filter-pattern '{ $.log = "ties-request" && $.status >= 500 }'
  CloudWatch Logs Insights:  filter log = "ties-request" | stats count() by route, status
Problem with a reply? Send its X-Request-Id and the time to doctortheisen@gmail.com.
"""
FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><rect width="16" height="16" rx="3" fill="#1a1a1a"/>'
           '<text x="8" y="12.5" font-family="monospace" font-size="12" font-weight="bold" fill="#fff" text-anchor="middle">T</text></svg>\n')
STYLE = """body{font-family:ui-monospace,Menlo,Consolas,monospace;max-width:640px;margin:0 auto;padding:16px;line-height:1.5;background:#fff;color:#222}
a{color:#06c}h1{font-size:20px;margin:0 0 8px}h2{font-size:16px;margin:24px 0 6px;border-bottom:1px solid #999;padding-bottom:4px}
pre{white-space:pre-wrap;word-break:break-word;background:#f4f4f4;padding:10px;font-size:13px}code{word-break:break-all}
.dim{color:#777;font-size:13px}ul{padding-left:18px}li{margin:6px 0}
@media (prefers-color-scheme:dark){body{background:#1a1a1a;color:#ddd}a{color:#6cf}pre{background:#111}.dim{color:#999}}"""


def _is(rx, x):
    return isinstance(x, str) and rx.match(x) is not None


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


def _abstract(text):
    """The abstract of any catalog bytes: the first line as title, and which of the parts the text has."""
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)                   # front matter: its title, then the body
    t = re.search(r'^title:\s*"?(.*?)"?\s*$', m.group(1), re.M) if m else None
    title = t.group(1) if t else next((l.strip().lstrip("#").strip() for l in text[m.end() if m else 0:].splitlines() if l.strip()), "")
    return dict(title=title, parts=[p for p in PARTS if re.search(rf"^#*\s*{p}\b", text, re.M | re.I)])


def _listing():
    return [dict({k: e[k] for k in ("entry", "url", "sha256")}, **_abstract(e["bytes"])) for e in map(_entry, _entries())]


def _object(hexid):
    """One object by content address: objects/<hex> if its bytes hash to <hex>, else a named entry with that sha256."""
    if not HEX.match(hexid or ""):
        return None
    path = os.path.join(OBJECTS, hexid)
    data = open(path, "rb").read() if os.path.isfile(path) else None
    if data is None or hashlib.sha256(data).hexdigest() != hexid:
        e = next((e for e in map(_entry, _entries()) if e["sha256"] == "sha256:" + hexid), None)
        data = e["bytes"].encode("utf-8") if e else None
    if data is None:
        return None
    text = data.decode("utf-8", "replace")
    return dict(id="sha256:" + hexid, url=f"{HOME}/catalog/sha256:{hexid}", sha256="sha256:" + hexid, bytes=text, **_abstract(text))


def _objects():
    names = sorted(os.listdir(OBJECTS)) if os.path.isdir(OBJECTS) else []
    out = [_object(f) for f in names if HEX.match(f)]
    return [{k: o[k] for k in ("id", "url", "title", "parts")} for o in out if o]


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
           f'<p class="dim"><a href="/">tiesapi.com</a> · <a href="/catalog">catalog</a> · <a href="/api">api</a> · <a href="/docs">docs</a></p>'
           f'{body}'
           f'<p class="dim">Dr. Theisen · <a href="mailto:{CONTACT}">{CONTACT}</a></p></body></html>\n')
    return _raw(status, "text/html; charset=utf-8", doc)


def _parts(x):
    return " · ".join(x["parts"]) if x["parts"] else "no parts"


def _entry_items():
    h = html.escape
    return "".join(f'<li><a href="/catalog/{h(e["entry"])}">{h(e["entry"])}</a> {h(e["title"])}<br>'
                   f'<span class="dim">{h(e["sha256"])} · {h(_parts(e))}</span></li>'
                   for e in _listing()) or '<li class="dim">none yet</li>'


def _object_items():
    h = html.escape
    return "".join(f'<li><a href="/catalog/{h(o["id"])}">{h(o["title"] or o["id"])}</a><br>'
                   f'<span class="dim">{h(o["id"])} · {h(_parts(o))}</span></li>'
                   for o in _objects()) or '<li class="dim">none yet</li>'


def _keyword_items():
    h = html.escape
    return "".join(f'<li>{h(k["keyword"])} <span class="dim">{h(k["definition"])}</span></li>' for k in _keywords()) \
        or '<li class="dim">none yet</li>'


def _home_html():
    h = html.escape
    return _page(200, "TIES", (
        "<h1>TIES</h1>"
        f"<h2>the calls</h2><pre>{h(CALLS)}</pre>"
        f"<h2>the catalog</h2><ul>{_entry_items()}</ul>"))


def _catalog_html():
    return _page(200, "TIES · catalog", (
        f"<h1>the catalog</h1><h2>named entries</h2><ul>{_entry_items()}</ul>"
        f"<h2>objects, by content address</h2><ul>{_object_items()}</ul>"))


def _entry_html(e):
    h = html.escape
    title = _abstract(e["bytes"])["title"] or e.get("entry") or e["id"]
    return _page(200, f"TIES · {title}", (
        f"<h1>{h(title)}</h1><p class=\"dim\">{h(e['url'])}<br>{h(e['sha256'])}</p>"
        f"<pre>{h(e['bytes'])}</pre>"))


def _docs():
    return dict(url=f"{HOME}/docs", sha256="sha256:" + hashlib.sha256(DOCS.encode("utf-8")).hexdigest(), bytes=DOCS)


def _not_found_html(path):
    return _page(404, "TIES · not found", f"<h1>not found</h1><p class=\"dim\">{html.escape(path)}</p><p><a href=\"/\">tiesapi.com</a></p>")


# ---- contracts: one route = one contract = one file = one page, in one form ---------------------------------------
# The names (contracts/, /api, route, handler, memo, builds on, example, versions) are his word.

FORM = """One route = one contract = one file, catalog/contracts/<name>, and one page, https://tiesapi.com/api/<name>.
The file, in this form (the API reads it; it knows nothing of any page's subject):

# <METHOD> <path>: <title, one line>
route     GET /path  or  POST /path  (a path may end in <name>: the rest of the path)
handler   the general operation that answers the route: one of the handlers listed with this form
memo      the public memo of the base; fixed once written: the base and every version hash it
input
  what the route takes
processing
  the steps, in order
output
  what comes out, and how a person checks it
graph
  builds on <name>        one line for each contract this one builds on
example
  GET /path               requests the code runs on every view of the page; a POST has its JSON body on the
  POST /path {...}        same line; {{1.receipt}} is the field receipt of reply 1
versions
  sha256:<64 hex> <memo>  append only: one line per version of everything above the line "versions"

The page shows input, processing and output from the file; the graph; the example requests and the replies the code
gives to them on that view; and the file, byte-exact.
The graph:
  base       sha256 of {"url":"https://tiesapi.com/api/<name>","memo":"<memo>"}: no content yet
  version k  sha256 of {"content":"sha256:<hex>","memo":"<memo>","prev":"<hash of the link before>"}
  head       the hash of the last version (the base while there is none); a session cites the head
  current    true when the last version's content is the sha256 of the file above "versions"
  builds on, built on by   the other contracts, each by URL and head
Hashes are sha256 of compact JSON (separators "," and ":", keys in the order shown, UTF-8 as is).
Add a contract: write the file; nothing else changes. GET /api/<name> then shows the base alone and content_sha256;
append under versions the line   sha256:<content_sha256> <memo>
Change a contract: edit above "versions" (never the memo line), then append one line. Never change or remove a line under
versions. Two contracts may not declare the same route: the second is refused.
Hash the content yourself:  sed '/^versions$/,$d' catalog/contracts/<name> | shasum -a 256"""
SECTIONS = ("input", "processing", "output", "graph", "example")
HEAD_KEYS = ("route", "handler", "memo")
ROUTE = re.compile(r"(GET|POST) (/[A-Za-z0-9_.:/-]*(?:<[a-z]+>)?)")
VERSION = re.compile(r"[ \t]*(sha256:[0-9a-f]{64})(?:[ \t]+(.*?))?[ \t]*")
REQUEST = re.compile(r"[ \t]*(GET|POST) (/\S*)[ \t]*(.*)")
BUILTIN = [("GET", "/", "home"), ("GET", "/docs", "docs"), ("GET", "/catalog", "catalog"), ("GET", "/catalog/sha256:<hex>", "object"),
           ("GET", "/catalog/<entry>", "entry"), ("GET", "/api", "contracts"), ("GET", "/api/<contract>", "contract"),
           ("GET", "/robots.txt", "robots"), ("GET", "/favicon.ico", "favicon"),
           ("POST", "/receipt", "receipt"), ("POST", "/verify", "verify"), ("POST", "/sop/check", "sop_check")]


def _sha(b):
    return "sha256:" + hashlib.sha256(b).hexdigest()


def _compact(d):
    return json.dumps(d, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _contract_names():
    try:
        return sorted(f for f in os.listdir(CONTRACTS) if SEGMENT.match(f) and os.path.isfile(os.path.join(CONTRACTS, f)))
    except OSError:
        return []


def _chain(url, memo, versions, content_sha256):
    """The base, then one link per version: each hashes the content, the memo and the link before. Append only."""
    base = dict(url=url, memo=memo)
    prev = base_hash = _sha(_compact(base))
    links = []
    for n, (content, vmemo) in enumerate(versions, 1):
        link = dict(content=content, memo=vmemo, prev=prev)
        prev = _sha(_compact(link))
        links.append(dict(n=n, **link, hash=prev))
    return dict(base=dict(base, hash=base_hash), versions=links, head=prev,
                current=bool(links) and links[-1]["content"] == content_sha256)


def _contract(name):
    """One contract file, read: its form and its chain. None if there is no such file. A file that does not follow the form
    comes back with "error" and routes nothing."""
    if name not in _contract_names():
        return None
    try:
        data = open(os.path.join(CONTRACTS, name), "rb").read()
    except OSError:
        return None
    m = re.search(rb"\nversions(?:\n|\Z)", data)                   # the line "versions", also as the file's last line
    content, tail = (data[:m.start() + 1], data[m.end():]) if m else (data, b"")
    c = dict(name=name, url=f"{HOME}/api/{name}", title="", route="", handler="", memo="", input="", processing="", output="",
             builds_on=[], notes=[], example=[], content_sha256=_sha(content), sha256=_sha(data), bytes=data.decode("utf-8", "replace"))
    try:
        lines, chain = content.decode("utf-8").splitlines(), tail.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return dict(c, error="the file is not UTF-8", chain=_chain(c["url"], "", [], c["content_sha256"]))
    if lines and lines[0].startswith("#"):
        c["title"], lines = lines[0].lstrip("#").strip(), lines[1:]
    sec, body = None, {k: [] for k in SECTIONS}
    for l in lines:
        if l in SECTIONS:
            sec = l
        elif sec:
            body[sec].append(l[2:] if l.startswith("  ") else l)
        else:
            c.update({k: l[len(k):].strip() for k in HEAD_KEYS if l.startswith(k + " ")})
    c.update({k: "\n".join(body[k]).strip("\n") for k in ("input", "processing", "output")})
    for l in body["graph"]:
        m = re.fullmatch(r"[ \t]*builds on[ \t]+(\S+)[ \t]*", l)
        if m:
            c["builds_on"].append(m.group(1))
        elif l.strip():
            c["notes"].append(l.strip())
    for l in body["example"]:
        m = REQUEST.fullmatch(l)
        if m:
            c["example"].append([m.group(1), m.group(2), m.group(3)])
        elif c["example"] and l.strip():
            c["example"][-1][2] += " " + l.strip()                 # a long JSON body may go on over the next lines
    versions, odd = [], []
    for l in chain:
        m = VERSION.fullmatch(l)
        if m:
            versions.append((m.group(1), m.group(2) or ""))
        elif l.strip():
            odd.append(l.strip()[:40])
    c["chain"] = _chain(c["url"], c["memo"], versions, c["content_sha256"])
    builtin = {f"{m} {p}": h for m, p, h in BUILTIN}
    err = ("route must be GET or POST and a path, such as GET /path or GET /path/<name>" if not ROUTE.fullmatch(c["route"]) else None) \
        or (f"handler must be one of: {', '.join(HANDLERS)}" if c["handler"] not in HANDLERS else None) \
        or (f"{c['route']} is built in, with handler {builtin[c['route']]}" if builtin.get(c["route"], c["handler"]) != c["handler"] else None) \
        or next((f"missing part: {k}" for k in SECTIONS if k not in lines), None) \
        or next((f"empty part: {k}" for k in ("input", "processing", "output") if not c[k]), None) \
        or ("the example has no request: a line GET /path or POST /path {...}" if not c["example"] else None) \
        or (f"a line under versions is not 'sha256:<64 hex> <memo>': {odd[0]}" if odd else None)
    return dict(c, error=err) if err else c


def _contracts():
    """Every contract file here, read. Never raises."""
    out = []
    for n in _contract_names():
        try:
            c = _contract(n)
        except Exception as e:                                       # listed with its error, never a 500
            c = dict(name=n, url=f"{HOME}/api/{n}", route="", title="", error=f"the file could not be read: {type(e).__name__}",
                     builds_on=[], chain=_chain(f"{HOME}/api/{n}", "", [], ""))
        if c:
            first = next((x for x in out if not x.get("error") and x["route"] == c["route"]), None)
            if first and not c.get("error"):                     # one route, one contract: the first by name keeps it
                c = dict(c, error=f"{c['route']} is already the route of contract {first['name']}")
            out.append(c)
    return out


def _graph(c, every):
    """The graph of one page: its chain, and the contracts it builds on and is built on by, each by URL and head; as nodes and
    edges too."""
    g = dict(c["chain"])
    heads = {x["name"]: x["chain"]["head"] for x in every}
    g["builds_on"] = [dict(name=n, url=f"{HOME}/api/{n}", head=heads.get(n)) for n in c["builds_on"]]
    g["built_on_by"] = [dict(name=x["name"], url=x["url"], head=x["chain"]["head"]) for x in every
                        if c["name"] in x.get("builds_on", []) and not x.get("error")]
    nodes = [dict(id=g["base"]["hash"], kind="base", url=c["url"], memo=g["base"]["memo"])]
    nodes += [dict(id=v["hash"], kind="version", n=v["n"], content=v["content"], memo=v["memo"]) for v in g["versions"]]
    edges = [{"from": v["hash"], "to": v["prev"], "rel": "prev"} for v in g["versions"]]
    for b, out in [(b, True) for b in g["builds_on"]] + [(b, False) for b in g["built_on_by"]]:
        if b["head"] and b["head"] != g["head"]:
            if b["head"] not in [x["id"] for x in nodes]:
                nodes.append(dict(id=b["head"], kind="page", url=b["url"]))
            edges.append({"from": g["head"], "to": b["head"], "rel": "builds on"} if out else {"from": b["head"], "to": g["head"], "rel": "builds on"})
    return dict(g, nodes=nodes, edges=edges, notes=c.get("notes", []))


def _run_examples(c):
    """Run the example requests of a contract through the router, now, in order; {{k.field}} is the field of reply k.
    Nothing is logged for them: they are part of the page's request."""
    out, replies = [], []
    def fill(s):
        def one(m):
            k = int(m.group(1))
            v = replies[k - 1].get(m.group(2)) if 0 < k <= len(replies) else None
            return v if isinstance(v, str) else m.group(0)
        return re.sub(r"\{\{([0-9]+)\.([A-Za-z_][A-Za-z0-9_]*)\}\}", one, s)
    for method, path, body in c["example"]:
        path, body = fill(path), fill(body)
        try:
            r = _route(method, path, False, {"body": body, "ties_example": True})
            status = r["statusCode"]
            reply = json.loads(r["body"]) if r["headers"].get("Content-Type") == "application/json" else r["body"]
        except Exception as e:
            status, reply = 500, dict(error=f"the example failed in the code: {type(e).__name__}")
        replies.append(reply if isinstance(reply, dict) else {})
        try:
            shown = json.loads(body) if body else None
        except ValueError:
            shown = body
        out.append(dict(request=dict(method=method, path=path, body=shown), reply=dict(status=status, body=reply)))
    return out


def _contract_page(c, every, run=True):
    p = {k: c.get(k, "") for k in ("name", "url", "route", "handler", "title", "memo", "input", "processing", "output")}
    p["graph"] = _graph(c, every)
    if run and not c.get("error"):
        p["example"] = _run_examples(c)
    else:
        p["example"] = []
        p["example_note"] = "not run: " + ("the contract does not follow the form" if c.get("error") else "an example inside an example")
    p.update({k: c.get(k, "") for k in ("content_sha256", "sha256", "bytes")})
    if c.get("error"):
        p["error"] = c["error"]
    return p


def _cut(x):
    if isinstance(x, str) and len(x) > 400:
        return x[:400] + f" … ({len(x)} characters; the whole reply is in the JSON of this page)"
    if isinstance(x, dict):
        return {k: _cut(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_cut(v) for v in x]
    return x


def _example_text(e):
    q, r = e["request"], e["reply"]
    show = lambda b: b if isinstance(b, str) else json.dumps(_cut(b), ensure_ascii=False, indent=2)
    return (f"request  {q['method']} {q['path']}" + ("" if q["body"] is None else "\n" + show(q["body"]))
            + f"\n\nreply    {r['status']}\n{show(r['body'])}")


def _graph_text(p):
    g = p["graph"]
    out = [f"base        {g['base']['url']}", f"            memo  {g['base']['memo']}", f"            {g['base']['hash']}"]
    if not g["versions"]:
        out.append("            no content yet: the base alone")
    for v in g["versions"]:
        out += ["  ^ prev", f"version {v['n']:<3} content {v['content']}", f"            memo  {v['memo']}",
                f"            prev  {v['prev']}", f"            {v['hash']}"]
    out.append(f"head        {g['head']}" + ("  (current: the last version is this file's content)" if g["current"]
                                              else f"  (not current: content {p['content_sha256']} is not a version yet)"))
    out += [f"builds on   {b['url']}  {b['head'] or 'no such contract here'}" for b in g["builds_on"]]
    out += [f"built on by {b['url']}  {b['head']}" for b in g["built_on_by"]]
    out += [f"note        {n}" for n in g["notes"]]
    return "\n".join(out)


def _contract_html(p):
    h = html.escape
    g = p["graph"]
    related = "".join(f'<li>{rel} <a href="/api/{h(b["name"])}">{h(b["name"])}</a> <span class="dim">{h(b["head"] or "")}</span></li>'
                      for rel, key in (("builds on", "builds_on"), ("built on by", "built_on_by")) for b in g[key])
    ex = "".join(f"<pre>{h(_example_text(e))}</pre>" for e in p["example"]) or f'<p class="dim">{h(p.get("example_note", ""))}</p>'
    return _page(200, f"TIES · {p['route'] or p['name']}", (
        f"<h1>{h(p['route'] or p['name'])}</h1><p>{h(p['title'])}</p>"
        f"<p class=\"dim\">{h(p['url'])}<br>head {h(g['head'])}</p>"
        + (f"<p><b>this contract does not follow the form:</b> {h(p['error'])}</p>" if p.get("error") else "")
        + f"<h2>input</h2><pre>{h(p['input'])}</pre>"
        f"<h2>processing</h2><pre>{h(p['processing'])}</pre>"
        f"<h2>output</h2><pre>{h(p['output'])}</pre>"
        f"<h2>graph</h2><pre>{h(_graph_text(p))}</pre>" + (f"<ul>{related}</ul>" if related else "")
        + f"<h2>example</h2><p class=\"dim\">the requests below were run by the code for this view; the replies are what it gave</p>{ex}"
        f"<h2>the file</h2><p class=\"dim\">catalog/contracts/{h(p['name'])} · {h(p['sha256'])}<br>content {h(p['content_sha256'])}</p>"
        f"<pre>{h(p['bytes'])}</pre>"))


def _contracts_html(every):
    h = html.escape
    items = "".join(f'<li><a href="/api/{h(c["name"])}">{h(c["route"] or c["name"])}</a> {h(c["title"])}<br>'
                    f'<span class="dim">head {h(c["chain"]["head"])}'
                    + (" · current" if c["chain"]["current"] else " · not current") + (f' · error: {h(c["error"])}' if c.get("error") else "")
                    + "</span></li>" for c in every) or '<li class="dim">no contract files here</li>'
    return _page(200, "TIES · api", (
        f"<h1>the API: one page per contract</h1><h2>contracts</h2><ul>{items}</ul>"
        f"<h2>the form</h2><pre>{h(FORM)}</pre><h2>handlers</h2><pre>{h(', '.join(HANDLERS))}</pre>"))


# ---- receipts -----------------------------------------------------------------------------------------------------

def _sign(fid, h, issued_at, label_sha256=None):
    # every field is format-checked before signing (FID, TIE_HASH, ISSUED_AT), so none can contain the separator
    msg = f"ties-receipt-v1|{fid}|{h}|{issued_at}|{label_sha256}" if label_sha256 else f"ties-receipt-v0|{fid}|{h}|{issued_at}"
    return hmac.new(_key(), msg.encode(), hashlib.sha256).hexdigest()


def _check_line(fid, line):
    """The line must be JSON, hash to its own hash, and come from this federated id. Returns (hash, extra, error):
    extra is {label, label_sha256, inside} for a canonical entry line, {} for a v0 tie line."""
    try:
        d = json.loads(line) if isinstance(line, str) else dict(line)
        assert isinstance(d, dict)
        h = d.pop("hash", None)
        body = json.dumps(d, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:                                    # a lone surrogate (\ud800) parses as JSON but is not UTF-8
        return None, None, "line has text that is not valid unicode (a lone surrogate)"
    except Exception:
        return None, None, "line is not a JSON object"
    if h != "sha256:" + hashlib.sha256(body).hexdigest():
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
    name = label[len(prefix):].rstrip("/")
    if name.startswith("sha256:") and HEX.match(name[7:]):
        e = dict(sha256=name)        # a content address is its own label: valid before any name or bytes are on this site
    else:
        e = _entry(name)
    if not e:
        return None, "label is not in the catalog"
    if not _is(TIE_HASH, d.get("inside")):
        return None, "inside must be sha256:<64 hex> of your content"
    parents = d.get("parents")
    if not isinstance(parents, list) or not all(_is(TIE_HASH, x) for x in parents):
        return None, "parents must be a list of sha256:<64 hex>"
    if d.get("by") not in ("human", "agent", "both"):
        return None, "by must be human, agent or both"
    if not isinstance(d.get("memo", ""), str):
        return None, "memo must be text"
    n, prev = d.get("n"), d.get("prev")
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        return None, "n must be the line number, from 1"
    if n == 1 and (prev is not None or label not in (f"{prefix}registration", f"{prefix}{d['inside']}")):
        return None, "line 1 is the registration: label .../catalog/registration or .../catalog/<its inside>, prev null"
    if n > 1 and not _is(TIE_HASH, prev):
        return None, "prev must be the hash of the line before"
    err = _check_run(d)
    if err:
        return None, err
    return dict(label=label, label_sha256=e["sha256"], inside=d["inside"]), None


def _check_run(d):
    """A line of one SOP run (catalog sop-nonce-chain): step 0 opens the run with the anchor as its nonce; step k>0 shows
    nonce x and the nonce before it, and sha256(x) must be that nonce before. A skipped, repeated or reordered step fails."""
    if not any(k in d for k in ("sop", "step", "nonce", "prev_nonce")):
        return None
    if not _is(TIE_HASH, d.get("sop")):
        return "sop must be sha256:<64 hex>, the content address of the SOP"
    k = d.get("step")
    if not isinstance(k, int) or isinstance(k, bool) or k < 0:
        return "step must be 0 (open) or the step number"
    if not _is(HEX, d.get("nonce")):
        return "nonce must be 64 hex"
    if k > 0:
        if not _is(HEX, d.get("prev_nonce")):
            return "prev_nonce must be 64 hex"
        if hashlib.sha256(bytes.fromhex(d["nonce"])).hexdigest() != d["prev_nonce"]:
            return f"step {k}: nonce does not follow the step before (skipped, repeated or out of order)"
    return None


def _sop_check(fid, sop, lines, receipts=None):
    """Check one run against its SOP, statelessly. sop = the SOP's text; its steps are the lines '<k> sha256:<hex>'.
    valid = every step 1..N present, in order, each bound to the one before. With receipts (one per line, from POST /receipt):
    each must verify, and issued_at must not go backwards, so the order is also an order in time."""
    if not isinstance(sop, str) or not isinstance(lines, list) or not (receipts is None or isinstance(receipts, list)):
        return dict(valid=False, error="send sop (the SOP's text), lines (the run's lines, in order) and optionally receipts")
    try:
        sid = "sha256:" + hashlib.sha256(sop.encode("utf-8")).hexdigest()
    except UnicodeEncodeError:
        return dict(valid=False, error="sop has text that is not valid unicode (a lone surrogate)")
    steps, odd = [], []
    for row in sop.splitlines():
        m = STEP.fullmatch(row)
        if m:
            steps.append((int(m.group(1)), "sha256:" + m.group(2)))
        elif re.match(r"[ \t]*[0-9]", row):
            odd.append(row.strip()[:40])
    if odd:
        return dict(valid=False, sop=sid, error=f"a line starts like a step but is not '<k> sha256:<hex>': {odd[0]}")
    if not steps or [k for k, _ in steps] != list(range(1, len(steps) + 1)):
        return dict(valid=False, sop=sid, error="the SOP lists no steps 1..N as lines '<k> sha256:<hex>'")
    if receipts is not None and len(receipts) != len(lines):
        return dict(valid=False, sop=sid, error="receipts must be one per line")
    out = dict(sop=sid, steps=len(steps), done=0, timed=receipts is not None)
    last_at = ""
    prev = None
    for i, line in enumerate(lines):
        h, extra, err = _check_line(fid, line)
        if err:
            return dict(out, valid=False, error=f"line {i}: {err}")
        d = json.loads(line) if isinstance(line, str) else dict(line)
        want = f"{HOME}/catalog/{sid if i == 0 else steps[i - 1][1]}" if i <= len(steps) else None
        err = ("more lines than steps" if want is None else None) \
            or ("not a run line" if "step" not in d else None) \
            or (f"line {i}: sop is not this SOP" if d.get("sop") != sid else None) \
            or (f"line {i}: step is {d.get('step')}, expected {i}" if d.get("step") != i else None) \
            or (f"step {i}: label is not step {i} of the SOP" if d.get("label") != want else None) \
            or (f"step {i}: prev_nonce is not the nonce of step {i - 1}" if i and d.get("prev_nonce") != prev["nonce"] else None) \
            or (f"step {i}: parents do not include step {i - 1}" if i and prev["hash"] not in (d.get("parents") or []) else None)
        if not err and receipts is not None:
            r = receipts[i] if isinstance(receipts[i], dict) else {}
            at, sig = r.get("issued_at"), r.get("receipt")
            if not (_is(ISSUED_AT, at) and isinstance(sig, str) and re.fullmatch(r"[0-9a-f]{64}", sig)
                    and hmac.compare_digest(_sign(fid, h, at, extra.get("label_sha256")), sig)):
                err = f"step {i}: no valid receipt for this line"
            elif at < last_at:
                err = f"step {i}: receipt issued before the step before ({at} < {last_at})"
            else:
                last_at = at
        if err:
            return dict(out, valid=False, complete=False, error=err)
        out["done"] = i
        prev = dict(d, hash=h)
    complete = bool(lines) and out["done"] == len(steps)
    return dict(out, valid=complete, complete=complete,
                **({} if complete else dict(error=f"incomplete: {out['done']} of {len(steps)} steps")))


def _check_tie(d):
    """A v0 tie line (SPEC.md)."""
    if d.get("frame") != "tie":
        return "not a tie line (frame) and not an entry line (label)"
    if not _is(KEYWORD, d.get("field")):
        return "keyword is not a word"
    try:
        v = json.loads(d.get("value"))
        assert isinstance(v, dict)
    except Exception:
        return "value is not a JSON object"
    if not (_is(ADDR, v.get("from")) and _is(ADDR, v.get("to"))):
        return "from/to are not addresses"
    if v.get("visibility") not in ("private", "public"):
        return "visibility must be private or public"
    return None


# ---- the handler --------------------------------------------------------------------------------------------------

def lambda_handler(event, context=None):
    t0 = time.monotonic()
    rid = getattr(context, "aws_request_id", None) or "local-" + uuid.uuid4().hex[:12]
    method = (((event.get("requestContext") or {}).get("http") or {}).get("method") or event.get("httpMethod") or "GET").upper()
    path = event.get("rawPath") or event.get("path") or "/"
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    browser = "text/html" in (headers.get("accept") or "")
    head = method == "HEAD"
    if head:
        method = "GET"
    log = dict(log="ties-request", request_id=rid, method="HEAD" if head else method if method in ("GET", "POST") else "other",
               route=_route_name(method, path), browser=browser, code=CODE[7:19])
    try:
        r = _route(method, path, browser, event)
    except Exception as e:                                      # never the gateway's bare 500; the message is not logged
        log.update(error_type=type(e).__name__,
                   where=[f"{os.path.basename(f.filename)}:{f.lineno}:{f.name}" for f in traceback.extract_tb(e.__traceback__)][-6:])
        r = _reply(500, dict(error=f"internal error; quote request_id {rid} to {CONTACT}", request_id=rid))
    if log["route"] in ("POST /verify", "POST /sop/check") and r["statusCode"] == 200:
        log["valid"] = json.loads(r["body"]).get("valid")
    r = dict(r, headers=dict(r["headers"], **{"X-Request-Id": rid, "Access-Control-Expose-Headers": "X-Request-Id"}))
    if head:
        r = dict(r, body="")
    b = event.get("body")
    log.update(status=r["statusCode"], ms=round((time.monotonic() - t0) * 1000, 1),
               in_bytes=len(b.encode("utf-8", "replace")) if isinstance(b, str) else 0, out_bytes=len(r["body"].encode("utf-8", "replace")))
    _log(log)
    return r


def _match(template, path):
    """A route template matches a path exactly, or, when it ends in <name>, every path that starts with what is before it."""
    if "<" in template:
        return path.startswith(template[:template.index("<")])
    return path == template or (template == "/" and path == "")


def _table():
    """The routes, in order: the built-in ones (so the bundle answers with no contract file at all), then the route of every
    contract file that follows the form and is not built in. Adding a contract adds its route here; this code does not change.
    Never raises."""
    out = list(BUILTIN)
    try:
        for c in _contracts():
            if not c.get("error"):
                m, p = c["route"].split(" ", 1)
                if not any(m == x and p == y for x, y, _ in out):
                    out.append((m, p, c["handler"]))
    except Exception:
        pass
    return out


def _route_name(method, path):
    """The route a request matches, for the log line: a template, never the caller's data."""
    for m, template, _ in _table():
        if m == method and _match(template, path):
            return f"{m} {template}"
    return "none"


def _log(line):
    """One JSON line to stdout: on Lambda, CloudWatch Logs /aws/lambda/ties-api. A failure to log never fails the reply."""
    try:
        print(json.dumps(line, separators=(",", ":")), flush=True)
    except Exception:
        pass


def _route(method, path, browser, event):
    for m, template, handler in _table():
        if m == method and _match(template, path):
            arg = path[len(template[:template.index("<")]):].rstrip("/") if "<" in template else path
            return HANDLERS[handler](arg, path, browser, event)
    return _not_found_html(path) if browser else _reply(404, dict(error="no such route"))


# ---- the handlers: the general operations a route (built in, or a contract's) runs ----------------------------------
# Each takes (arg, path, browser, event); arg is the rest of the path for a template that ends in <name>, else the path.

def _h_home(arg, path, browser, event):
    if browser:
        return _home_html()
    return _reply(200, dict(name="TIES API", authority=AUTHORITY, contact=CONTACT, docs=f"{HOME}/docs", api=f"{HOME}/api",
                            code_sha256=CODE, routes=ROUTES, receipt={"federated_id": "theisen-api:<16 hex>", "line": ENTRY}))


def _h_docs(arg, path, browser, event):
    return _entry_html(_docs()) if browser else _reply(200, dict(authority=AUTHORITY, **_docs()))


def _h_catalog(arg, path, browser, event):
    return _catalog_html() if browser else _reply(200, dict(authority=AUTHORITY, keywords=_keywords(), entries=_listing(),
                                                             objects=_objects()))


def _h_object(arg, path, browser, event):
    o = _object(arg)
    if o:
        return _entry_html(o) if browser else _reply(200, dict(authority=AUTHORITY, **o))
    return _not_found_html(path) if browser else _reply(404, dict(error="no bytes for this content address here; "
                                                                         "the id is still valid as a label"))


def _h_entry(arg, path, browser, event):
    e = _entry(arg)
    if e:
        return _entry_html(e) if browser else _reply(200, dict(authority=AUTHORITY, **e))
    return _not_found_html(path) if browser else _reply(404, dict(error="no such catalog entry"))


def _h_robots(arg, path, browser, event):
    return _raw(200, "text/plain; charset=utf-8", "User-agent: *\nAllow: /\n")


def _h_favicon(arg, path, browser, event):
    return _raw(200, "image/svg+xml", FAVICON)


def _h_contracts(arg, path, browser, event):
    every = _contracts()
    if browser:
        return _contracts_html(every)
    return _reply(200, dict(authority=AUTHORITY, url=f"{HOME}/api", form=FORM, handlers=list(HANDLERS), contracts=[
        dict({k: c.get(k, "") for k in ("name", "url", "route", "title")}, head=c["chain"]["head"], current=c["chain"]["current"],
             **({"error": c["error"]} if c.get("error") else {})) for c in every]))


def _h_contract(arg, path, browser, event):
    every = _contracts()
    c = next((x for x in every if x["name"] == arg), None)
    if not c:
        return _not_found_html(path) if browser else _reply(404, dict(error="no such contract"))
    p = _contract_page(c, every, run=not event.get("ties_example"))
    return _contract_html(p) if browser else _reply(200, dict(authority=AUTHORITY, **p))


def _request(event, limit):
    """The JSON body of a POST and its federated id: (request, federated id, None), or (None, None, the refusal)."""
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        import base64
        try:
            raw = base64.b64decode(raw).decode("utf-8", "replace")
        except Exception:
            return None, None, _reply(400, dict(error="body is not valid base64"))
    if len(raw) > limit:
        return None, None, _reply(413, dict(error="body too large"))
    try:
        req = json.loads(raw)
        assert isinstance(req, dict)
    except Exception:
        return None, None, _reply(400, dict(error="send a JSON object"))
    fid = req.get("federated_id") or ""
    if not _is(FID, fid):
        return None, None, _reply(400, dict(error="federated_id must look like theisen-api:<16 hex>"))
    return req, fid, None


def _h_sop_check(arg, path, browser, event):
    req, fid, refused = _request(event, MAX_SOP_BODY)
    return refused or _reply(200, _sop_check(fid, req.get("sop"), req.get("lines"), req.get("receipts")))


def _h_receipt(arg, path, browser, event):
    req, fid, refused = _request(event, MAX_BODY)
    if refused:
        return refused
    h, extra, err = _check_line(fid, req.get("line"))
    if err:
        return _reply(400, dict(error=err, entry=ENTRY))
    issued_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _reply(201, dict(receipt=_sign(fid, h, issued_at, extra.get("label_sha256")), issued_at=issued_at, hash=h, **extra,
                            federated_id=fid, authority=AUTHORITY, stored=False, attests=(ATTESTS_ADDRESS if extra.get("label", "").startswith(f"{HOME}/catalog/sha256:") else ATTESTS_ENTRY) if extra else ATTESTS,
                            contact=CONTACT))


def _h_verify(arg, path, browser, event):
    req, fid, refused = _request(event, MAX_BODY)
    if refused:
        return refused
    h = req.get("hash") or req.get("tie_hash") or ""
    issued_at, receipt, ls = req.get("issued_at") or "", req.get("receipt") or "", req.get("label_sha256")
    if not (_is(TIE_HASH, h) and _is(ISSUED_AT, issued_at) and isinstance(receipt, str) and re.fullmatch(r"[0-9a-f]{64}", receipt)):
        return _reply(400, dict(error="hash, issued_at or receipt is not in the expected form"))
    if ls is not None and not _is(TIE_HASH, ls):
        return _reply(400, dict(error="label_sha256 must be sha256:<64 hex> (from the receipt)"))
    valid = hmac.compare_digest(_sign(fid, h, issued_at, ls), receipt)
    out = dict(valid=valid, federated_id=fid, hash=h, issued_at=issued_at)
    if ls:
        out["label_sha256"] = ls
    return _reply(200, dict(**out, attests=ATTESTS_LABEL if ls else ATTESTS))


HANDLERS = {"home": _h_home, "docs": _h_docs, "catalog": _h_catalog, "object": _h_object, "entry": _h_entry, "robots": _h_robots,
            "favicon": _h_favicon, "contracts": _h_contracts, "contract": _h_contract, "receipt": _h_receipt, "verify": _h_verify,
            "sop_check": _h_sop_check}
