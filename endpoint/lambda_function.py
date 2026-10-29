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
  GET  /robots.txt, /favicon.ico. HEAD answers like GET, without the body.

The catalog is read from catalog/ beside this file (the published bundle) or ../catalog (the repository).
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
GET  /docs              the API reference: every call, its fields and errors, and what is logged"""
ROUTES = ["GET /docs", "GET /catalog", "GET /catalog/<entry>", "GET /catalog/sha256:<hex>", "POST /receipt", "POST /verify",
          "POST /sop/check"]
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

## Examples
  curl https://tiesapi.com/
  curl -s https://tiesapi.com/docs | jq -r .bytes
  curl https://tiesapi.com/catalog
  curl -s -X POST https://tiesapi.com/receipt -H 'Content-Type: application/json' \
       -d "$(jq -cn --arg f "$FID" --arg l "$LINE" '{federated_id:$f, line:$l}')"
On a device: bin/ties enter <label> <inside> writes a line; bin/ties receipt <hash> gets its receipt; bin/ties verify
checks the chain.

## Logging
Where: CloudWatch Logs, log group /aws/lambda/ties-api, region us-east-1. Retention: as set on the log group (none set as of
2026-10-28: kept until deleted).
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
           f'<p class="dim"><a href="/">tiesapi.com</a> · <a href="/catalog">catalog</a> · <a href="/docs">docs</a></p>'
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


def _route_name(method, path):
    """The route a request matches, for the log line: a template, never the caller's data."""
    if method == "GET":
        if path in ("/", ""):
            return "GET /"
        if path in ("/docs", "/catalog", "/robots.txt", "/favicon.ico"):
            return f"GET {path}"
        if path.startswith("/catalog/sha256:"):
            return "GET /catalog/sha256:<hex>"
        if path.startswith("/catalog/"):
            return "GET /catalog/<entry>"
    if method == "POST" and path in ("/receipt", "/verify", "/sop/check"):
        return f"POST {path}"
    return "none"


def _log(line):
    """One JSON line to stdout: on Lambda, CloudWatch Logs /aws/lambda/ties-api. A failure to log never fails the reply."""
    try:
        print(json.dumps(line, separators=(",", ":")), flush=True)
    except Exception:
        pass


def _route(method, path, browser, event):
    if method == "GET" and path in ("/", ""):
        if browser:
            return _home_html()
        return _reply(200, dict(name="TIES API", authority=AUTHORITY, contact=CONTACT, docs=f"{HOME}/docs", code_sha256=CODE,
                                routes=ROUTES, receipt={"federated_id": "theisen-api:<16 hex>", "line": ENTRY}))
    if method == "GET" and path == "/docs":
        return _entry_html(_docs()) if browser else _reply(200, dict(authority=AUTHORITY, **_docs()))
    if method == "GET" and path == "/catalog":
        return _catalog_html() if browser else _reply(200, dict(authority=AUTHORITY, keywords=_keywords(), entries=_listing(),
                                                                 objects=_objects()))
    if method == "GET" and path.startswith("/catalog/sha256:"):
        o = _object(path[len("/catalog/sha256:"):].rstrip("/"))
        if o:
            return _entry_html(o) if browser else _reply(200, dict(authority=AUTHORITY, **o))
        return _not_found_html(path) if browser else _reply(404, dict(error="no bytes for this content address here; "
                                                                             "the id is still valid as a label"))
    if method == "GET" and path.startswith("/catalog/"):
        e = _entry(path[len("/catalog/"):].rstrip("/"))
        if e:
            return _entry_html(e) if browser else _reply(200, dict(authority=AUTHORITY, **e))
        return _not_found_html(path) if browser else _reply(404, dict(error="no such catalog entry"))
    if method == "GET" and path == "/robots.txt":
        return _raw(200, "text/plain; charset=utf-8", "User-agent: *\nAllow: /\n")
    if method == "GET" and path == "/favicon.ico":
        return _raw(200, "image/svg+xml", FAVICON)
    if method != "POST" or path not in ("/receipt", "/verify", "/sop/check"):
        return _not_found_html(path) if browser else _reply(404, dict(error="no such route"))
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        import base64
        try:
            raw = base64.b64decode(raw).decode("utf-8", "replace")
        except Exception:
            return _reply(400, dict(error="body is not valid base64"))
    if len(raw) > (MAX_SOP_BODY if path == "/sop/check" else MAX_BODY):
        return _reply(413, dict(error="body too large"))
    try:
        req = json.loads(raw)
        assert isinstance(req, dict)
    except Exception:
        return _reply(400, dict(error="send a JSON object"))
    fid = req.get("federated_id") or ""
    if not _is(FID, fid):
        return _reply(400, dict(error="federated_id must look like theisen-api:<16 hex>"))
    if path == "/sop/check":
        return _reply(200, _sop_check(fid, req.get("sop"), req.get("lines"), req.get("receipts")))
    if path == "/receipt":
        h, extra, err = _check_line(fid, req.get("line"))
        if err:
            return _reply(400, dict(error=err, entry=ENTRY))
        issued_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return _reply(201, dict(receipt=_sign(fid, h, issued_at, extra.get("label_sha256")), issued_at=issued_at, hash=h, **extra,
                                federated_id=fid, authority=AUTHORITY, stored=False, attests=(ATTESTS_ADDRESS if extra.get("label", "").startswith(f"{HOME}/catalog/sha256:") else ATTESTS_ENTRY) if extra else ATTESTS,
                                contact=CONTACT))
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
