#!/bin/bash
# gate: one check per user story in SPEC.md. Scratch files only; the receipt endpoint runs on localhost.
set -u; cd "$(dirname "$0")"; pass=0; fail=0
ok() { echo "ok   $1"; pass=$((pass+1)); }; bad() { echo "FAIL $1"; fail=$((fail+1)); }
T="$(mktemp -d)"; S="$T/ties.jsonl"; X=bin/ties; PORT=${PORT:-8799}
export TIES_KEY="$(openssl rand -hex 32)"
(cd endpoint && exec python3 serve_local.py $PORT) >/dev/null 2>&1 & EP=$!; trap 'kill $EP 2>/dev/null; rm -rf "$T"' EXIT
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
C="$T/register.jsonl"; CAT=https://tiesapi.com/catalog; EP="http://127.0.0.1:$PORT"
printf 'Test Operator, test@example.org, iPhone, Example University, lab manager, annual review\n' > "$T/reg.txt"; RI="$($X hash "$T/reg.txt")"
H1="$($X enter "$CAT/registration" "$RI" --memo registered --store "$C")"
echo "step 3: pick the label, fetch it, check its hash" > "$T/s1.txt"; S1="$($X hash "$T/s1.txt")"
H2="$($X enter "$CAT/sop-001" "$S1" --parent "$H1" --memo "SOP-001 step 3" --by both --store "$C")"
FID2="$($X id --store "$C")"
echo "$FID2" | grep -Eq '^theisen-api:[0-9a-f]{16}$' && $X verify --store "$C" >/dev/null && [ "$($X lines --store "$C" | wc -l | tr -d ' ')" = 2 ] && ok "US-8 canonical register: registration on line 1, a labeled entry with a parent, chain verifies ($FID2)" || bad "US-8 register"
R2="$($X receipt "$H2" --endpoint "$EP" --store "$C")"
LS="sha256:$(shasum -a 256 catalog/entries/sop-001 | cut -c1-64)"
[ "$(printf '%s' "$R2" | jq -r .label_sha256)" = "$LS" ] && [ "$(printf '%s' "$R2" | jq -r .inside)" = "$S1" ] && [ "$(printf '%s' "$R2" | jq -r .label)" = "$CAT/sop-001" ] && ok "US-8 receipt binds the label (and the catalog's sha256 of it), the inside and the time" || bad "US-8 receipt: $R2"
V2="$(printf '%s' "$R2" | jq -c --arg f "$FID2" '{federated_id:$f, hash, issued_at, receipt}' | curl -s -d @- "$EP/verify" | jq -r .valid)"; [ "$V2" = true ] && ok "US-8 receipt verifies" || bad "US-8 verify"
C2="$T/r2.jsonl"; $X enter "$CAT/registration" "$RI" --store "$C2" >/dev/null; H3="$($X enter "$CAT/not-a-label" "$S1" --store "$C2")"
E3="$(jq -cn --arg f "$($X id --store "$C2")" --arg l "$(jq -c --arg h "$H3" 'select(.hash==$h)' "$C2")" '{federated_id:$f, line:$l}' | curl -s -d @- "$EP/receipt" | jq -r .error)"
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
SH="sha256:$(shasum -a 256 catalog/entries/sop-001 | cut -c1-64)"
[ "$(curl -s "$H/catalog" | jq -r '.entries[] | select(.entry=="sop-001") | .sha256')" = "$SH" ] && ok "GET /catalog lists sop-001 with the sha256 of its file" || bad "catalog listing"
[ "$(curl -s "$H/catalog/sop-001" | jq -j .bytes | shasum -a 256 | cut -c1-64)" = "${SH#sha256:}" ] && ok "GET /catalog/sop-001 returns the exact bytes (they hash to the same sha256)" || bad "catalog entry bytes"
curl -s -H 'Accept: text/html' "$H/catalog/sop-001" | grep -q '<pre>' && ok "GET /catalog/sop-001 for a browser is the page" || bad "catalog entry page"
[ "$(curl -s -o /dev/null -w '%{http_code}' "$H/catalog/no-such-entry")" = 404 ] && ok "an unknown entry is 404" || bad "entry 404"
[ "$(curl -s -o /dev/null -w '%{http_code}' -I "$H/")" = 200 ] && ok "HEAD / is 200" || bad "HEAD"
[ "$(curl -s -o /dev/null -w '%{http_code} %{content_type}' "$H/favicon.ico")" = "200 image/svg+xml" ] && [ "$(curl -s -o /dev/null -w '%{http_code}' "$H/robots.txt")" = 200 ] && ok "favicon.ico and robots.txt" || bad "favicon/robots"
echo "$pass passed, $fail failed"; [ $fail -eq 0 ]
