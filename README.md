# TIES

TIES records the ties between things, not the things. You keep your things and your chain of ties;
each thing is named by its content address (sha256). A tie says: this connects to that, in this way,
from this operator, at this time, with a public memo.

- The site: https://tiesapi.com (a browser gets pages; curl gets JSON)
- API reference: https://tiesapi.com/docs — every route, its fields, replies and errors, the limits, the SOP run check, and what is logged. The text is `DOCS` in [endpoint/lambda_function.py](endpoint/lambda_function.py), so it ships with the code; `GET /` gives `code_sha256` to check which code is live.
- The API, one page per route: https://tiesapi.com/api — each route is a contract, one file in [catalog/contracts/](catalog/contracts/), in one form: input, processing, output, graph, example, versions. https://tiesapi.com/api/<name> shows the parts, a worked example the code runs on that view, and the graph: the base, every version (append only), and the contracts it builds on and is built on by.
- The catalog: [catalog/entries/](catalog/entries/), one file per label, written by the operator; the file name is the URL, https://tiesapi.com/catalog/<name>. Drafts stay in drafts/ on the laptop: ignored by git, never pushed.
- Spec and user stories: [SPEC.md](SPEC.md)
- On your device: `bin/ties` (enter, lines, receipt, verify for the canonical register; register, add, context, sop, export for v0 ties)
- Receipts: https://tiesapi.com (stores nothing)
- Logs: one JSON line per request in CloudWatch Logs, group /aws/lambda/ties-api (us-east-1), with no request content; every reply carries X-Request-Id, the id on its log line. Details: https://tiesapi.com/docs, Logging.
- Check it yourself: `./test.sh` runs one check per user story, then the site, page API, SOP run, docs and logging checks
- Publish: `bin/publish` tests, bundles the endpoint and the catalog, sends them to tiesapi.com, and compares every entry's sha256 from the outside

## The workflow: put a page on tiesapi.com

1. Write the file. One file = one entry = one URL. Put it at `catalog/entries/<name>`; the exact bytes are what the site shows and hashes.
2. Run `bin/publish`. It runs the checks, bundles, sends, then fetches every entry back from https://tiesapi.com and compares the sha256.
3. Open https://tiesapi.com/catalog/<name>. A browser sees the page; `curl` sees the bytes and the sha256 as JSON.

To change an entry, change the file and publish again; the hash changes with it. To take one down, remove the file and publish.
The same three steps, with a button, are on the local catalog page (`~/Developer/tiesapi/cms`, port 8800).

## Add a contract (a route and its page)

1. Write one file, `catalog/contracts/<name>`, in the form that https://tiesapi.com/api (or `GET /api` locally) gives: route, handler, memo, then input, processing, output, graph, example. The handler is one of the general operations listed there; nothing in `endpoint/lambda_function.py` changes.
2. Open `/api/<name>` on a local server (`TIES_KEY=$(openssl rand -hex 32) python3 endpoint/serve_local.py 8798`). The page shows the base alone (no content yet), the example run by the code, and `content_sha256`.
3. Append the first version under the file's last line: `versions`, then `  sha256:<content_sha256> <memo>`. Hash it yourself with `sed '/^versions$/,$d' catalog/contracts/<name> | shasum -a 256`.
4. `./test.sh`: every contract must follow the form, its example must answer below 400 (and with valid true where a reply has valid), and its last version must be its content. To change a contract later, edit above `versions` (never the memo line: the base hashes it) and append one line; never change a line already there. Two contracts may not declare the same route.

`bin/publish` today bundles `catalog/keywords.tsv` and `catalog/entries/` only, not `catalog/contracts/`: the built-in routes answer without the files, but the pages under /api go live only when bin/publish also copies `catalog/contracts/`.

Authority: theisen-api, Dr. Theisen · https://doctortheisen.com/ · Problem? doctortheisen@gmail.com, with the X-Request-Id of the reply
