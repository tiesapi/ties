#!/bin/bash
# gate: one check per user story in SPEC.md. Scratch files only; the receipt endpoint runs on localhost.
set -u; cd "$(dirname "$0")"; pass=0; fail=0
ok() { echo "ok   $1"; pass=$((pass+1)); }; bad() { echo "FAIL $1"; fail=$((fail+1)); }
T="$(mktemp -d)"; S="$T/ties.jsonl"; X=bin/ties; PORT=${PORT:-8799}
export TIES_KEY="$(openssl rand -hex 32)"
(cd endpoint && exec python3 serve_local.py $PORT) & EP=$!; trap 'kill $EP 2>/dev/null; rm -rf "$T"' EXIT
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
V="$(printf '%s' "$R" | jq -c --arg f "$FID" '{federated_id:$f, tie_hash, issued_at, receipt}' | curl -s -d @- "http://127.0.0.1:$PORT/verify" | jq -r .valid)"
[ "$V" = true ] && ok "US-7 receipt issued and verified ($(printf '%s' "$R" | jq -r .receipt | cut -c1-12)…), nothing stored" || bad "US-7 receipt"
TAMP="$(jq -c --argjson n "$TH" 'select(.n==$n) | .field="changed"' "$S")"
[ "$(jq -cn --arg f "$FID" --arg l "$TAMP" '{federated_id:$f, line:$l}' | curl -s -d @- "http://127.0.0.1:$PORT/receipt" | jq .status)" = 400 ] && ok "US-7 a changed tie is refused" || bad "US-7 tamper"
# guards
$X add "$FID" "bad word" "$P" --store "$S" 2>/dev/null; [ $? -eq 2 ] && ok "a keyword with a space is refused" || bad "keyword guard"
$X add "not an address" opens "$P" --store "$S" 2>/dev/null; [ $? -eq 2 ] && ok "a from that is not an address is refused" || bad "address guard"
printf 'opens\topen a session\n' > "$T/kw.tsv"; $X add "$FID" responsibiliTIES "$P" --catalog "$T/kw.tsv" --store "$S" 2>/dev/null; [ $? -eq 2 ] && ok "with a catalog, a keyword not in it is refused" || bad "catalog guard"
echo "$pass passed, $fail failed"; [ $fail -eq 0 ]
