# Local extraction quality repair

User reports content from unrelated PDF sections in topics, stages, competency descriptions and literature. Preserve previous local corrections and do not publish or push.

- [x] Regression corpus from saved PDF225/237/239 (text/tables/SHA), plus synthetic boundary and preservation cases; observe failures before implementation.
- [x] Preserve table row continuations and refuse nonadjacent continuation tables. Use table columns or explicit topic/stage markers, never arbitrary numbered headings as fallback.
- [x] Bound bibliography sections, keep genuine years/volumes/math numbers; attach field sources/pages and actionable extraction warnings.
- [x] Apply shared validation to saved/legacy display; questionable fragments stay out of normal content and trigger a plain notice linking source PDF. Propagate warnings to needs_review status for future runs.
- [x] Reprocess saved RPD/practice PDFs offline, verify SHA, preserve source PDFs and version history, rebuild program contexts. Back up DB first.
- [x] Run targeted and full tests, independent review, rebuild local Docker and verify actual screenshots/pages. No GitHub updates.

No guarantee is made for arbitrary PDF semantics; validators cover specified structural failure modes. Prefer visible incomplete/review status over fabricated clean text.

Validation: 15 structural regression tests (including real text/table corpus225/237/239 with SHA), original PDFs visually checked. Independent review found an additional bibliography-heading variant in227; covered by failing regression then fixed. Full offline reparse160savedPDFs; run10 creates new primary/final versions, originals and history preserved. Results150processed,8needs_review,1partial,1skipped(type mismatch144). All160document pages respond200; screenshot cases now3stages,7math topics,7ML topics. Semester-header audit recovered missing semesters in37documents from table header cells with split words, including237:1,2. No pushes or Pages updates.

Final full suite: 96 passed, 1 existing Starlette deprecation warning. Current Docker image includes semester-header normalization; browser verified237semesters1,2 and7MLtopics, corrected practice3stages. All160saved-document pages checked over HTTP.
