# OOP summary repair implementation plan

**Goal:** Replace noisy OCR excerpts with a concise, factual program description, including when no LLM is connected.
**Approved approach:** Clean OOP extraction, summarize structured facts through the configured LLM, validate before display; use a factual fallback without an LLM. Reprocess the saved OOP PDFs and rebuild the local app.
**Constraints:** Preserve the previous discipline-link fix, PDF provenance, primary/final JSON version history, and existing provider settings. Do not invent topics or practical-training locations.

- [x] Add regression tests for scanned contents pages, structured OOP facts, LLM input/output, and page fallback.
- [x] Skip contents pages during section extraction and parse qualification, duration, professional areas and task types.
- [x] Add a shared program-summary module for a restricted fact projection, output checks and deterministic fallback.
- [x] Apply the projection/validation in LLM and page generation; increment the parser version.
- [x] Run targeted and full tests and review the diff.
- [x] Back up the database, reprocess the three saved OOP PDFs with new version records, rebuild Docker, and inspect the AI program page.

Validation: 68 tests passed. Three saved OOP PDFs reprocessed in run 9 with new primary/final versions; original PDFs and history preserved. Docker is healthy. Browser and HTTP checks confirm clean descriptions (360-425 chars) and working discipline links. LLM remains disabled; provider output checks are covered with controlled API responses.
