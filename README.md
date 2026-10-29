# TIES

TIES records the ties between things, not the things. You keep your things and your chain of ties;
each thing is named by its content address (sha256). A tie says: this connects to that, in this way,
from this operator, at this time, with a public memo.

- The site: https://tiesapi.com (a browser gets pages; curl gets JSON)
- API reference: https://tiesapi.com/docs — every route, its fields, replies and errors, the limits, the SOP run check, and what is logged. The text is `DOCS` in [endpoint/lambda_function.py](endpoint/lambda_function.py), so it ships with the code; `GET /` gives `code_sha256` to check which code is live.
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

Authority: theisen-api, Dr. Theisen · https://doctortheisen.com/ · Problem? doctortheisen@gmail.com, with the X-Request-Id of the reply
