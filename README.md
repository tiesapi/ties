# TIES

TIES records the ties between things, not the things. You keep your things and your chain of ties;
each thing is named by its content address (sha256). A tie says: this connects to that, in this way,
from this operator, at this time, with a public memo.

- The site: https://tiesapi.com (a browser gets pages; curl gets JSON)
- The catalog: [catalog/entries/](catalog/entries/), one file per label, written by the operator; the file name is the URL, https://tiesapi.com/catalog/<name>. Agent drafts stay in [drafts/](drafts/) and are not published.
- Spec and user stories: [SPEC.md](SPEC.md)
- On your device: `bin/ties` (enter, lines, receipt, verify for the canonical register; register, add, context, sop, export for v0 ties)
- Receipts: https://tiesapi.com (stores nothing)
- Check it yourself: `./test.sh` runs one check per user story, then the site checks
- Publish: `bin/publish` tests, bundles the endpoint and the catalog, sends them to tiesapi.com, and compares every entry's sha256 from the outside

Authority: theisen-api, Dr. Theisen · https://doctortheisen.com/ · Problem? doctortheisen@gmail.com
