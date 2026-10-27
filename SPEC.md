# TIES API v0

Authority: theisen-api (Dr. Theisen; https://doctortheisen.com/ ; https://github.com/doctortheisen/). Home: tiesapi.com.
Problem? Email doctortheisen@gmail.com

TIES records the ties between things, not the things. The things stay with the person who has them,
named by content address. A tie says: this connects to that, in this way, from this operator, at this time.

## The tie

One line on the operator's own chain (the operator-register line format, so one verifier checks both):

    {"n":7,"when":"2026-10-26T22:01:05-0700","who":"theisen-api:91494f84e6b0b7b7","frame":"tie",
     "field":"<keyword>","value":"{\"from\":\"<address>\",\"to\":\"<address>\",\"visibility\":\"private\",\"memo\":\"...\",\"attrs\":{}}",
     "prev":"sha256:...","hash":"sha256:..."}

- keyword   the descriptive tie, in doctortheisen's words (responsibiliTIES, possibiliTIES, realiTIES, ...).
            The valid keywords are a catalog: catalog/keywords.tsv. [[ his ]]
- from, to  addresses: sha256:<64 hex> (content), theisen-api:<16 hex> (an operator), or <scheme>:<id>.
            from = to is allowed: a directed tie about the object itself (it adds information about that object).
- visibility  private (default; stays on the device) | public (tie, addresses and memo may be published).
- memo      the public memo. Never put private details in it.
- attrs     tags, attributes, fidelity checks the operator selects. Private ties may carry personal context here.
- hash      sha256 of the compact line without "hash"; prev = the hash of the line before. The chain is the history.

The person hashes their own content (`ties hash <file>` or any sha256 tool). Nothing has to be sent anywhere.

## Receipt (tiesapi.com, stores nothing)

POST a tie line with its federated id; the endpoint checks the form and returns a receipt:
receipt = HMAC-SHA256(authority key, federated id | tie hash | issued_at). It is about who, which tie and when,
not only the content. v0 receipts are checked by the authority (`POST /verify`).

What a v0 receipt proves: this tie line was presented at issued_at and hashes to tie_hash. It does not prove who made
the tie: the federated id is as claimed. Proof of who needs a key per operator (the federated id derived from a public
key, each tie signed by its private key). [[ v1 ]]

## User stories (each has a check; `./test.sh` runs them all)

US-1 Register (required first).
  As an operator, I register myself (physical universe frame, virtual identity, institutional frame,
  roles and responsibilities, my worldline) so my ties start from a federated id under theisen-api.
  Check: `ties register` writes the registration lines and prints the federated id; `ties verify` passes.

US-2 Open my worldline sandbox.
  As an operator, I open a clean, timestamped workspace for a working session.
  Check: `timenow-sandbox` opens one sandbox; `ties add <fid> <keyword> <sandbox address>` ties it to me.

US-3 Tie my context.
  As an operator, I tie my responsibilities to a project within my institutional frame,
  so what needs to happen in the real world is stated.
  Check: ties from my federated id to the project's address, and from the project to the institution, verify.

US-4 Write an SOP as sequential ties.
  As a human writing a standard operating procedure, I create ties in order; each step is a tie, the order is the chain.
  Check: `ties sop <address>` lists the steps in order.

US-5 Hand my ties to an AI assistant.
  As an operator working with an AI tool, I give it my ties; it looks up the addresses in my local library
  and register and knows the context at once.
  Check: `ties context <address>` prints every tie touching that address, both directions.

US-6 Ask Dr. Theisen.
  As an operator with a question, I ship my ties (not my files) so the connections are visible.
  Check: `ties export` writes my chain; the receiver's `ties verify` passes; private memos and attrs are
  dropped from `ties export --public`.

US-7 Get a receipt.
  As an operator, I get a receipt from tiesapi.com for a tie, without anything being stored there.
  Check: the endpoint returns a receipt; `/verify` accepts it; a changed tie is refused.
