"""tiesapi.com — TIES receipt endpoint. Stores nothing. Python standard library only.

  GET  /            what this is, in plain words (JSON)
  GET  /catalog     the public tie keywords (catalog/keywords.tsv bundled at deploy)
  POST /receipt     {"federated_id", "line"}  -> {"receipt", "issued_at", "tie_hash", ...}
  POST /verify      {"federated_id", "tie_hash", "issued_at", "receipt"} -> {"valid": true|false}

receipt = HMAC-SHA256(TIES_KEY, federated_id | tie_hash | issued_at), hex.
Every reply carries "status" in the body as well as the HTTP status.
"""
import hashlib, hmac, json, os, re
from datetime import datetime, timezone

AUTHORITY = "theisen-api"
CONTACT = "doctortheisen@gmail.com"
FID = re.compile(r"^theisen-api:[0-9a-f]{16}$")
ADDR = re.compile(r"^(sha256:[0-9a-f]{64}|theisen-api:[0-9a-f]{16}|[a-z][a-z0-9+.-]*:\S+)$")
KEYWORD = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
MAX_BODY = 16 * 1024
HERE = os.path.dirname(os.path.abspath(__file__))


def _key():
    k = os.environ.get("TIES_KEY", "")
    if len(k) < 32:
        raise RuntimeError("TIES_KEY is not set")
    return k.encode()


def _catalog():
    path = os.path.join(HERE, "keywords.tsv")
    out = []
    if os.path.exists(path):
        for row in open(path, encoding="utf-8"):
            if row.strip() and not row.startswith("#"):
                kw, _, definition = row.rstrip("\n").partition("\t")
                out.append({"keyword": kw, "definition": definition})
    return out


def _reply(status, body):
    body = dict(status=status, **body)
    return {"statusCode": status, "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
            "body": json.dumps(body, ensure_ascii=False, indent=2) + "\n"}


def _sign(fid, tie_hash, issued_at):
    return hmac.new(_key(), f"{fid}|{tie_hash}|{issued_at}".encode(), hashlib.sha256).hexdigest()


def _check_line(fid, line):
    """The tie line must be well formed, hash to its own hash, and come from this federated id."""
    try:
        d = json.loads(line) if isinstance(line, str) else dict(line)
    except (ValueError, TypeError):
        return None, "line is not JSON"
    h = d.pop("hash", None)
    body = json.dumps(d, separators=(",", ":"), ensure_ascii=False)
    if h != "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest():
        return None, "line hash does not recompute"
    if d.get("frame") != "tie" or d.get("who") != fid:
        return None, "not a tie from this federated id"
    if not KEYWORD.match(d.get("field") or ""):
        return None, "keyword is not a word"
    try:
        v = json.loads(d.get("value") or "")
    except ValueError:
        return None, "value is not JSON"
    if not (ADDR.match(v.get("from") or "") and ADDR.match(v.get("to") or "")):
        return None, "from/to are not addresses"
    if v.get("visibility") not in ("private", "public"):
        return None, "visibility must be private or public"
    return h, None


def lambda_handler(event, context=None):
    method = (event.get("requestContext", {}).get("http", {}).get("method") or event.get("httpMethod") or "GET").upper()
    path = event.get("rawPath") or event.get("path") or "/"
    if method == "GET" and path in ("/", ""):
        return _reply(200, dict(
            name="TIES API", authority=AUTHORITY, contact=CONTACT,
            what="Records the ties between things, not the things. You keep your things and your chain; "
                 "send a tie line here to get a receipt. Nothing is stored.",
            spec="https://tiesapi.com/spec", routes=["GET /catalog", "POST /receipt", "POST /verify"]))
    if method == "GET" and path == "/catalog":
        return _reply(200, dict(authority=AUTHORITY, keywords=_catalog()))
    if method != "POST" or path not in ("/receipt", "/verify"):
        return _reply(404, dict(error="no such route"))
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
        tie_hash, err = _check_line(fid, req.get("line"))
        if err:
            return _reply(400, dict(error=err))
        issued_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return _reply(201, dict(receipt=_sign(fid, tie_hash, issued_at), issued_at=issued_at, tie_hash=tie_hash,
                                federated_id=fid, authority=AUTHORITY, stored=False, contact=CONTACT))
    tie_hash, issued_at, receipt = req.get("tie_hash") or "", req.get("issued_at") or "", req.get("receipt") or ""
    valid = bool(receipt) and hmac.compare_digest(_sign(fid, tie_hash, issued_at), receipt)
    return _reply(200, dict(valid=valid, federated_id=fid, tie_hash=tie_hash, issued_at=issued_at))
