#!/bin/bash
# gate: one check per user story in SPEC.md. Scratch files only; the receipt endpoint runs on localhost.
set -u; cd "$(dirname "$0")"; pass=0; fail=0
ok() { echo "ok   $1"; pass=$((pass+1)); }; bad() { echo "FAIL $1"; fail=$((fail+1)); }
T="$(mktemp -d)"; S="$T/ties.jsonl"; X=bin/ties; PORT=${PORT:-8799}
export TIES_KEY="$(openssl rand -hex 32)"
curl -s -o /dev/null --max-time 1 "http://127.0.0.1:$PORT/" && { echo "FAIL port $PORT is already in use (a stale serve_local.py?); stop it first"; exit 1; }
# the run's own catalog: two scratch labels; the public catalog holds only what the operator wrote
mkdir -p "$T/catalog/entries"; cp catalog/keywords.tsv "$T/catalog/"
printf 'registration: scratch label for the test run\n' > "$T/catalog/entries/registration"; printf 'sop-001: scratch label for the test run\n' > "$T/catalog/entries/sop-001"
TIES_CATALOG="$T/catalog" python3 endpoint/serve_local.py $PORT >/dev/null 2>&1 & EP=$!; trap 'kill $EP 2>/dev/null; rm -rf "$T"' EXIT
for i in $(seq 1 20); do curl -s -o /dev/null --max-time 1 "http://127.0.0.1:$PORT/" && break; sleep 0.3; done

# US-1 register
printf 'Test Operator\ntest@example.org\niPhone\n\n\nExample University\nResearch and teaching\nLab manager\nAnnual review\nIRB\nResearch record\n' | $X register --store "$S" >"$T/reg.out" 2>/dev/null
FID="$($X id --store "$S")"
echo "$FID" | grep -Eq '^theisen-api:[0-9a-f]{16}$' && $X verify --store "$S" >/dev/null && ok "US-1 register: federated id $FID, chain verifies" || bad "US-1 register"
# US-2 worldline sandbox
mkdir -p "$T/wl/days"; SB="$(bin/timenow-sandbox --root "$T/wl" --criterion "first session" | jq -r '.[] | arrays | select(.[0]=="WHERE") | .[1]')"
RH="$($X hash "$SB/README.md")"
$X add "$FID" opens "$RH" --memo "first working session" --store "$S" >/dev/null && $X context "$RH" --store "$S" | grep -q opens && ok "US-2 sandbox opened and tied to me ($RH)" || bad "US-2 sandbox"
# US-3 context: my responsibilities w.r.t. a project w.r.t. the institution (keywords are placeholders until his catalog)
echo "Annual safety report: signed report to the dean" > "$T/project.txt"; P="$($X hash "$T/project.txt")"
echo "Example University" > "$T/inst.txt"; I="$($X hash "$T/inst.txt")"
$X add "$FID" responsibiliTIES "$P" --memo "I deliver the annual safety report" --store "$S" >/dev/null
$X add "$P" withinFrame "$I" --memo "the report is for the university" --store "$S" >/dev/null
$X add "$P" describes "$P" --attr due=2026-12-15 --store "$S" >/dev/null
[ "$($X context "$P" --store "$S" | wc -l | tr -d ' ')" = 3 ] && $X verify --store "$S" >/dev/null && ok "US-3 responsibilities -> project -> institution, plus a self-tie on the project" || bad "US-3 context"
# US-4 SOP as sequential ties
SOP="sop:open-a-session"
for step in "say what you are trying to do" "run timenow-sandbox" "confirm the README" "rate it yourself"; do
  h="$(printf '%s' "$step" | shasum -a 256 | cut -c1-64)"; $X add "$SOP" step "sha256:$h" --memo "$step" --public --store "$S" >/dev/null; done
$X sop "$SOP" --store "$S" | awk -F'\t' 'NR==1 && $4=="say what you are trying to do"{a=1} NR==4 && $4=="rate it yourself"{b=1} END{exit !(a&&b)}' && ok "US-4 SOP: 4 steps listed in order" || bad "US-4 SOP"
# US-5 AI assistant: context of an address
$X context "$P" --store "$S" | grep -q 'responsibiliTIES' && $X context "$P" --store "$S" | grep -q 'withinFrame' && ok "US-5 context shows ties into and out of the project" || bad "US-5 context"
# US-6 ask Dr. Theisen: export, and public export
$X export --store "$S" --out "$T/shipped.jsonl" 2>/dev/null; $X verify --store "$T/shipped.jsonl" >/dev/null && ok "US-6 shipped chain verifies on the receiving side" || bad "US-6 export"
$X export --public --store "$S" --out "$T/public.jsonl" 2>/dev/null
[ "$(wc -l < "$T/public.jsonl" | tr -d ' ')" = 4 ] && ! grep -q 'Test Operator\|annual safety\|due' "$T/public.jsonl" && $X verify --lines --store "$T/public.jsonl" >/dev/null && ok "US-6 public export: only the 4 public SOP ties, no registration, no private memos, each line verifies" || bad "US-6 public export"
# US-7 receipt
TH="$($X context "$P" --store "$S" | head -1 | cut -f1)"; TL="$(jq -r --argjson n "$TH" 'select(.n==$n) | .hash' "$S")"
R="$($X receipt "$TL" --endpoint "http://127.0.0.1:$PORT" --store "$S")"
V="$(printf '%s' "$R" | jq -c --arg f "$FID" '{federated_id:$f, hash, issued_at, receipt}' | curl -s -d @- "http://127.0.0.1:$PORT/verify" | jq -r .valid)"
[ "$V" = true ] && ok "US-7 receipt issued and verified ($(printf '%s' "$R" | jq -r .receipt | cut -c1-12)…), nothing stored" || bad "US-7 receipt"
TAMP="$(jq -c --argjson n "$TH" 'select(.n==$n) | .field="changed"' "$S")"
[ "$(jq -cn --arg f "$FID" --arg l "$TAMP" '{federated_id:$f, line:$l}' | curl -s -d @- "http://127.0.0.1:$PORT/receipt" | jq .status)" = 400 ] && ok "US-7 a changed tie is refused" || bad "US-7 tamper"
# US-8 SOP-001, the canonical register: registration on line 1, a labeled entry with a parent, a receipt that binds label + inside + time
C="$T/register.jsonl"; CAT=https://tiesapi.com/catalog; EPURL="http://127.0.0.1:$PORT"
printf 'Test Operator, test@example.org, iPhone, Example University, lab manager, annual review\n' > "$T/reg.txt"; RI="$($X hash "$T/reg.txt")"
H1="$($X enter "$CAT/registration" "$RI" --memo registered --store "$C")"
echo "step 3: pick the label, fetch it, check its hash" > "$T/s1.txt"; S1="$($X hash "$T/s1.txt")"
H2="$($X enter "$CAT/sop-001" "$S1" --parent "$H1" --memo "SOP-001 step 3" --by both --store "$C")"
FID2="$($X id --store "$C")"
echo "$FID2" | grep -Eq '^theisen-api:[0-9a-f]{16}$' && $X verify --store "$C" >/dev/null && [ "$($X lines --store "$C" | wc -l | tr -d ' ')" = 2 ] && ok "US-8 canonical register: registration on line 1, a labeled entry with a parent, chain verifies ($FID2)" || bad "US-8 register"
R2="$($X receipt "$H2" --endpoint "$EPURL" --store "$C")"
LS="sha256:$(shasum -a 256 "$T/catalog/entries/sop-001" | cut -c1-64)"
[ "$(printf '%s' "$R2" | jq -r .label_sha256)" = "$LS" ] && [ "$(printf '%s' "$R2" | jq -r .inside)" = "$S1" ] && [ "$(printf '%s' "$R2" | jq -r .label)" = "$CAT/sop-001" ] && ok "US-8 receipt binds the label (and the catalog's sha256 of it), the inside and the time" || bad "US-8 receipt: $R2"
V2="$(printf '%s' "$R2" | jq -c --arg f "$FID2" '{federated_id:$f, hash, issued_at, receipt, label_sha256}' | curl -s -d @- "$EPURL/verify" | jq -r .valid)"; [ "$V2" = true ] && ok "US-8 receipt verifies (label_sha256 is inside the signature)" || bad "US-8 verify"
V3="$(printf '%s' "$R2" | jq -c --arg f "$FID2" '{federated_id:$f, hash, issued_at, receipt, label_sha256:"sha256:0000000000000000000000000000000000000000000000000000000000000000"}' | curl -s -d @- "$EPURL/verify" | jq -r .valid)"; [ "$V3" = false ] && ok "US-8 a changed label_sha256 is refused" || bad "US-8 label_sha256 tamper"
C2="$T/r2.jsonl"; $X enter "$CAT/registration" "$RI" --store "$C2" >/dev/null; H3="$($X enter "$CAT/not-a-label" "$S1" --store "$C2")"
E3="$(jq -cn --arg f "$($X id --store "$C2")" --arg l "$(jq -c --arg h "$H3" 'select(.hash==$h)' "$C2")" '{federated_id:$f, line:$l}' | curl -s -d @- "$EPURL/receipt" | jq -r .error)"
[ "$E3" = "label is not in the catalog" ] && ok "US-8 a label not in the catalog gets no receipt" || bad "US-8 label guard: $E3"
$X enter "$CAT/sop-001" "$S1" --store "$T/r3.jsonl" 2>/dev/null; [ $? -eq 2 ] && ok "US-8 line 1 must be the registration" || bad "US-8 line 1 guard"
# guards
$X add "$FID" "bad word" "$P" --store "$S" 2>/dev/null; [ $? -eq 2 ] && ok "a keyword with a space is refused" || bad "keyword guard"
$X add "not an address" opens "$P" --store "$S" 2>/dev/null; [ $? -eq 2 ] && ok "a from that is not an address is refused" || bad "address guard"
printf 'opens\topen a session\n' > "$T/kw.tsv"; $X add "$FID" responsibiliTIES "$P" --catalog "$T/kw.tsv" --store "$S" 2>/dev/null; [ $? -eq 2 ] && ok "with a catalog, a keyword not in it is refused" || bad "catalog guard"
# the website: a browser gets pages, curl gets JSON; the catalog entries hash to their files
H="http://127.0.0.1:$PORT"
[ "$(curl -s -o /dev/null -w '%{content_type}' -H 'Accept: text/html' "$H/")" = "text/html; charset=utf-8" ] && curl -s -H 'Accept: text/html' "$H/" | grep -q '<h1>TIES</h1>' && ok "GET / for a browser is the page" || bad "home page"
[ "$(curl -s "$H/" | jq -r '.routes | index("GET /catalog/<entry>")')" != null ] && ok "GET / for curl is JSON and lists GET /catalog/<entry>" || bad "home json"
SH="sha256:$(shasum -a 256 "$T/catalog/entries/sop-001" | cut -c1-64)"
[ "$(curl -s "$H/catalog" | jq -r '.entries[] | select(.entry=="sop-001") | .sha256')" = "$SH" ] && ok "GET /catalog lists sop-001 with the sha256 of its file" || bad "catalog listing"
[ "$(curl -s "$H/catalog/sop-001" | jq -j .bytes | shasum -a 256 | cut -c1-64)" = "${SH#sha256:}" ] && ok "GET /catalog/sop-001 returns the exact bytes (they hash to the same sha256)" || bad "catalog entry bytes"
curl -s -H 'Accept: text/html' "$H/catalog/sop-001" | grep -q '<pre>' && ok "GET /catalog/sop-001 for a browser is the page" || bad "catalog entry page"
[ "$(curl -s -o /dev/null -w '%{http_code}' "$H/catalog/no-such-entry")" = 404 ] && ok "an unknown entry is 404" || bad "entry 404"
[ "$(curl -s -o /dev/null -w '%{http_code}' -I "$H/")" = 200 ] && ok "HEAD / is 200" || bad "HEAD"
[ "$(curl -s -o /dev/null -w '%{http_code} %{content_type}' "$H/favicon.ico")" = "200 image/svg+xml" ] && [ "$(curl -s -o /dev/null -w '%{http_code}' "$H/robots.txt")" = 200 ] && ok "favicon.ico and robots.txt" || bad "favicon/robots"
# the catalog page API (objects by content address) and the SOP run check (POST /sop/check); scratch objects, scratch key
python3 - "$H" "$T/catalog/objects" > "$T/page.out" 2>&1 <<'PY' || bad "page API checks: the check script failed ($(tail -1 "$T/page.out"))"
import hashlib, json, os, sys, time, urllib.error, urllib.request
EP, OBJ = sys.argv[1], sys.argv[2]; CAT = "https://tiesapi.com/catalog"; H = lambda b: hashlib.sha256(b).hexdigest()
def check(name, cond): print(("ok   " if cond else "FAIL ") + name)
def req(path, body=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    try:
        r = urllib.request.urlopen(urllib.request.Request(EP + path, data, {"Content-Type": "application/json"}), timeout=5)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())
    except Exception as e: return "no reply", {"error": type(e).__name__}
def put(text):
    b = text.encode(); open(os.path.join(OBJ, H(b)), "wb").write(b); return "sha256:" + H(b)
def js(d): return json.dumps(d, separators=(",", ":"), ensure_ascii=False)
def line(**d): d["hash"] = "sha256:" + H(js(d).encode()); return d
def receipt(d): return req("/receipt", dict(federated_id=FID, line=js(d)))
def chain(x, m):
    for _ in range(m): x = H(bytes.fromhex(x))
    return x
def run(order, label_of={}):
    seed = H(os.urandom(32)); n = [chain(seed, 3 - k) for k in range(4)]
    out = [line(n=2, who=FID, label=f"{CAT}/{SID}", inside=SID, parents=[], memo="", by="agent", sop=SID, step=0, nonce=n[0], prev=REG)]
    for k in order:
        p = out[-1]
        out.append(line(n=len(out) + 2, who=FID, label=label_of.get(k, f"{CAT}/{steps[k - 1]}"), inside="sha256:" + H(f"result {k}".encode()),
                        parents=[p["hash"]], memo="", by="agent", sop=SID, step=k, nonce=n[k], prev_nonce=p["nonce"], prev=p["hash"]))
    return out
def sopcheck(lines, recs=None): return req("/sop/check", dict(federated_id=FID, sop=SOP, lines=[js(x) for x in lines], receipts=recs))[1]
os.makedirs(OBJ, exist_ok=True)
steps = [put(f"# step {k}: scratch step for the test run\n## input\na\n## processing\nb\n## output\nc\n") for k in (1, 2, 3)]
SOP = "# SOP: scratch SOP for the test run\n" + "".join(f"{k} {s}\n" for k, s in enumerate(steps, 1)); SID = put(SOP)
REG = "sha256:" + H(b"scratch registration"); FID = "theisen-api:" + H(("theisen-api|" + REG).encode())[:16]
o = {x["id"]: x for x in req("/catalog")[1].get("objects", [])}
check("GET /catalog lists objects by content address, with title and parts",
      o.get(SID, {}).get("title") == "SOP: scratch SOP for the test run" and o.get(steps[0], {}).get("parts") == ["input", "processing", "output"])
s, g = req(f"/catalog/{SID}"); check("GET /catalog/sha256:<hex> serves bytes that hash to the id", s == 200 and "sha256:" + H(g["bytes"].encode()) == SID)
check("an unknown content address is 404", req("/catalog/sha256:" + "0" * 64)[0] == 404)
free = "sha256:" + H(b"no bytes for this on the site")
s, r = receipt(line(n=2, who=FID, label=f"{CAT}/{free}", inside=free, parents=[], memo="", by="agent", prev=REG))
check("POST /receipt takes a content-address label with no bytes on the site", s == 201 and r.get("label_sha256") == free)
check("line 1 may be labeled by its own inside", receipt(line(n=1, who=FID, label=f"{CAT}/{REG}", inside=REG, parents=[], memo="", by="human", prev=None))[0] == 201)
good = run([1, 2, 3]); recs = [receipt(x)[1] for x in good]; r = sopcheck(good, recs)
check("POST /sop/check: the run done right is valid, complete and timed", r.get("valid") is True and r.get("complete") is True and r.get("timed") is True)
r = sopcheck(good); check("a run without receipts is valid but not timed", r.get("valid") is True and r.get("timed") is False)
for name, order, lab in [("a skipped step", [1, 3], {}), ("a repeated step", [1, 1, 2, 3], {}), ("steps out of order", [2, 1, 3], {}),
                         ("a wrong label", [1, 2, 3], {2: f"{CAT}/{steps[2]}"}), ("an incomplete run", [1, 2], {})]:
    x = run(order, lab); check(f"{name} is refused", sopcheck(x, [receipt(l)[1] for l in x]).get("valid") is False)
f = [dict(x) for x in recs]; f[2]["receipt"] = "0" * 64; check("a forged receipt is refused", sopcheck(good, f).get("valid") is False)
late = run([1, 2, 3]); early = receipt(late[2])[1]; time.sleep(1.1)
rs = [early if i == 2 else receipt(x)[1] for i, x in enumerate(late)]
check("genuine receipts issued out of time order are refused", "receipt issued before" in (sopcheck(late, rs).get("error") or ""))
base = {k: v for k, v in run([1])[1].items() if k != "hash"}
wrong = [f for f, v in [("sop", 5), ("step", "1"), ("step", True), ("nonce", 5), ("prev_nonce", None), ("parents", "x"), ("n", True)]
         if receipt(line(**dict(base, **{f: v})))[0] != 400]
check("wrong types in a correctly hashed line give 400", not wrong)
s, r = req("/receipt", raw=json.dumps(dict(federated_id=FID, line='{"who":"%s","memo":"\\ud800"}' % FID)).encode())
check("a lone surrogate in a /receipt line gives 400, not a 500", s == 400 and "lone surrogate" in (r.get("error") or ""))
s, r = req("/sop/check", raw=json.dumps(dict(federated_id=FID, sop="\ud800", lines=[])).encode())
check("a lone surrogate in the sop text of /sop/check is refused, not a 500", s == 200 and r.get("valid") is False and "lone surrogate" in (r.get("error") or ""))
PY
while IFS= read -r l; do case "$l" in "ok   "*) ok "${l#ok   }";; "FAIL "*) bad "${l#FAIL }";; esac; done < "$T/page.out"
[ "$(grep -c '^ok   \|^FAIL ' "$T/page.out")" = 17 ] || bad "page API checks: expected 17 results, got $(grep -c '^ok   \|^FAIL ' "$T/page.out")"
# docs, routes and logging: the handler in-process, scratch key, scratch catalog; every reply has an id and one log line
TIES_CATALOG="$T/catalog" python3 - endpoint > "$T/log.out" 2>&1 <<'PY' || bad "docs and logging checks: the check script failed ($(tail -1 "$T/log.out"))"
import base64, contextlib, hashlib, io, json, os, sys, types
sys.path.insert(0, sys.argv[1]); import lambda_function as lf
H = lambda b: hashlib.sha256(b).hexdigest(); FIELDS = {"log", "request_id", "method", "route", "status", "ms", "in_bytes", "out_bytes", "browser", "code"}
ALLOWED = FIELDS | {"valid", "error_type", "where"}
def check(name, cond): print(("ok   " if cond else "FAIL ") + name)
calls = []
def call(method, path, body=None, accept="", rid=None, b64=False):
    ev = {"requestContext": {"http": {"method": method}}, "rawPath": path, "headers": {"accept": accept}, "isBase64Encoded": b64, "body": body if isinstance(body, str) or body is None else json.dumps(body)}
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try: r = lf.lambda_handler(ev, types.SimpleNamespace(aws_request_id=rid or f"test-{len(calls)}"))
        except Exception as e: r = {"statusCode": "raised " + type(e).__name__, "headers": {}, "body": ""}
    logs = [json.loads(l) for l in out.getvalue().splitlines() if l.startswith("{")]
    calls.append((method, path, r, logs, out.getvalue())); return r, logs, out.getvalue()
def body(r): return json.loads(r["body"] or "{}") if isinstance(r["statusCode"], int) and r["body"] else {}
def js(d): return json.dumps(d, separators=(",", ":"), ensure_ascii=False)
def line(**d): d["hash"] = "sha256:" + H(js(d).encode()); return d
b = body(call("GET", "/docs")[0]); doc = b.get("bytes", "")
check("GET /docs serves the reference with the sha256 of its bytes", doc.startswith("# TIES API reference") and b.get("sha256") == "sha256:" + H(doc.encode()))
r = call("GET", "/docs", accept="text/html")[0]; check("GET /docs for a browser is the page", r["statusCode"] == 200 and "<pre>" in r["body"])
home = body(call("GET", "/")[0]); routes = home.get("routes", [])
example = {"GET /catalog/<entry>": "/catalog/no-such-entry", "GET /catalog/sha256:<hex>": "/catalog/sha256:" + "0" * 64}
listed = [" ".join(l.split()) + " " for l in doc.split("## Routes", 1)[-1].split("\n## ", 1)[0].splitlines()]
def answers(x):
    r = call(x.split()[0], example.get(x, x.split()[1]), body={})[0]
    return isinstance(r["statusCode"], int) and r["statusCode"] < 500 and body(r).get("error") != "no such route"
check("GET / names every route (docs included), the Routes list of the reference names each, and each answers below 500",
      "GET /docs" in routes and "POST /sop/check" in routes and all(any(l.startswith(x + " ") for l in listed) for x in routes)
      and all(answers(x) for x in routes))
code = "sha256:" + H(open(os.path.join(sys.argv[1], "lambda_function.py"), "rb").read())
check("GET / gives code_sha256, the sha256 of the running lambda_function.py", home.get("code_sha256") == code)
# a request of every kind, with markers in everything a caller sends
REG = "sha256:" + H(b"scratch registration for the log checks"); FID = "theisen-api:" + H(("theisen-api|" + REG).encode())[:16]
MARK = "zq-marker-7f3a"; entry = line(n=1, who=FID, label=f"https://tiesapi.com/catalog/{REG}", inside=REG, parents=[], memo=MARK, by="human", prev=None)
calls.clear()
rec = body(call("POST", "/receipt", dict(federated_id=FID, line=js(entry)))[0])
call("POST", "/verify", dict(federated_id=FID, hash=rec.get("hash"), issued_at=rec.get("issued_at"), receipt=rec.get("receipt"), label_sha256=rec.get("label_sha256")))
call("POST", "/receipt", dict(federated_id=FID, line=js(dict(entry, memo=MARK + "-changed"))))
call("POST", "/sop/check", dict(federated_id=FID, sop=f"# {MARK} sop\n1 sha256:{'1' * 64}\n", lines=[js(entry)]))
call("POST", "/receipt", "{not json " + MARK); call("POST", "/nowhere", {}); call("GET", "/"); call("GET", "/catalog"); call("HEAD", "/")
call("GET", "/catalog/no-such-entry", accept="text/html"); call("GET", "/docs")
call("GET", f"/catalog/sha256:{H(MARK.encode())}"); call("GET", f"/catalog/{MARK}"); call("GET", f"/{MARK}/{FID}"); call("PUT", f"/{MARK}")
rec2 = body(call("POST", "/receipt", base64.b64encode(js(dict(federated_id=FID, line=js(entry))).encode()).decode(), b64=True)[0])
each = all(len(l) == 1 and FIELDS <= set(l[0]) <= ALLOWED and l[0]["log"] == "ties-request" and l[0]["status"] == r["statusCode"]
           and l[0]["method"] == (m if m in ("GET", "HEAD", "POST") else "other") for m, p, r, l, _ in calls)
check(f"every request writes exactly one JSON log line, only the documented fields, the reply status ({len(calls)} requests)", each)
check("every reply carries X-Request-Id, the request_id of its log line",
      all(l and r["headers"].get("X-Request-Id") == l[0].get("request_id") for _, _, r, l, _ in calls))
check("/verify and /sop/check log valid", [l[0].get("valid") if l else None for m, p, r, l, _ in calls if p in ("/verify", "/sop/check")] == [True, False])
logtext = "".join(o for *_, o in calls)
sent = [MARK, FID, entry["hash"], REG, H(MARK.encode()), rec.get("receipt") or "no receipt", rec2.get("receipt") or "no receipt"]
check("no request content reaches the log: no path data, line, memo, hash, federated id, receipt or SOP text, base64 bodies and other methods included",
      not [s for s in sent if s in logtext] and rec2.get("status") == 201)
real = lf._route
def boom(*a): raise ValueError("boom " + MARK)
lf._route = boom
r, l, o = call("GET", "/", rid="test-boom"); lf._route = real; b = body(r)
check("an internal error answers JSON 500 with request_id; the log has the error type and place, not the message",
      r["statusCode"] == 500 and b.get("status") == 500 and b.get("request_id") == "test-boom" and len(l) == 1 and l[0].get("status") == 500
      and l[0].get("error_type") == "ValueError" and bool(l[0].get("where")) and MARK not in o)
real = lf._key
def nokey(): raise RuntimeError("TIES_KEY is not set")
lf._key = nokey
r, l, o = call("POST", "/receipt", dict(federated_id=FID, line=js(entry))); lf._key = real
check("a missing key answers JSON 500 with request_id, not a crash",
      r["statusCode"] == 500 and bool(body(r).get("request_id")) and len(l) == 1 and l[0].get("error_type") == "RuntimeError")
PY
while IFS= read -r l; do case "$l" in "ok   "*) ok "${l#ok   }";; "FAIL "*) bad "${l#FAIL }";; esac; done < "$T/log.out"
[ "$(grep -c '^ok   \|^FAIL ' "$T/log.out")" = 10 ] || bad "docs and logging checks: expected 10 results, got $(grep -c '^ok   \|^FAIL ' "$T/log.out")"
echo "$pass passed, $fail failed"; [ $fail -eq 0 ]
