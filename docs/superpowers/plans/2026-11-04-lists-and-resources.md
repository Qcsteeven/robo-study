# Readable practice lists and preserved document resources

User requires general extraction/display behavior for future programs and reprocessing. Local only; no GitHub/Pages updates.

- [x] Failing regressions: nested practice steps (numbered/bulleted, decimals/version preservation); all ML239 methodological links + source sections/page; generic other-program URLs, wrapped URLs, duplicates, hidden PDF URI annotations and unsafe URL schemes.
- [x] Add shared stage-list parsing, persisted during normal extraction and reused for legacy display.
- [x] Collect external resource URLs from complete source text and PDF URI annotations, not truncated display sections; preserve labels/origins and missing-address references without inventing URLs.
- [x] Render resources inside Literature with clickable URLs and original section/page notes; prevent duplicate display of links already in printed bibliography. Increment parser version for future reprocessing.
- [x] Upgrade saved documents with new resource metadata/step lists while preserving current fields, source hashes and history; no network scraping.
- [x] Run full suite, independent review, rebuild/restart local app, verify nested lists and ML resources in browser. No remote publication.

Validation: 107 tests passed (11 list/resource regressions), one existing Starlette deprecation warning. Independent review found heading/list ambiguity, long URI captions, missing bibliography origins, wrapped query parameters and a missing source-heading type; all reproduced by failing tests and fixed. Exact URL matching prevents distinct prefix URLs from disappearing. The standard pipeline now uses parser1.6; no program IDs or specialty-specific branches exist in the production implementation.

Local maintenance run11 refreshed168saved documents, preserving1497resource entries across documents and forming nested lists in10stages. Primary/final histories and source PDFs retained; backup /tmp/robo-study-before-lists-resources.sqlite. Older OOP/GIA parser versions retained when only resource metadata was added, so future full reprocessing is not bypassed. HTTP verified168document pages, ML links/origins/missing-address reference and practice225 sublists3/6/2. Browser verified readable resource notes and nested lists. Docker running updated image. No commits/pushes/Pages changes.
