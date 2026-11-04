# GitHub Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Publish saved program results for the teacher on GitHub Pages.
**Architecture:** Export existing FastAPI HTML from a temporary SQLite backup; crawl only public read routes; rewrite links and copy static assets; publish generated output to gh-pages.
**Tech Stack:** Python, FastAPI TestClient, BeautifulSoup, GitHub Pages.
**Spec:** docs/superpowers/specs/2026-11-04-github-pages-design.md

## Global Constraints
- Base path /robo-study/; preserve program/discipline navigation and source PDF links.
- Export latest available primary/final JSON, never database, secrets or logs.
- No robot execution during export; no writes to the source database.
- Clearly explain snapshot and local-only update behavior.

## Review Focus
- Query-based discipline and level links must remain distinct and work on static hosting.
- Asset URLs must stay under /robo-study/, including fonts loaded by CSS.
- Missing JSON versions must not create broken downloads.
- Existing output must not be deleted or silently combined with stale files.
- Empty database and unexpected internal URLs must fail visibly.

## Task 1: Static exporter and publication
**Files:** robot_op/export_pages.py, tests/test_export_pages.py, README.md
**Interfaces:** export_site(client, output, base_path, json_urls=()) -> dict; CLI --output --base-path, SQLite backup.
- [x] Write and run tests for query links, assets, downloads, backend-free update page and unexpected route rejection (RED).
- [x] Implement exporter, backup-based CLI, theme and snapshot notice (GREEN).
- [x] Run targeted tests, export current data, validate all local links, run full suite.
- [ ] Review code; commit source changes excluding data.demo-backup and server.log.
- [ ] Push source and gh-pages snapshot; enable Pages; verify deployed pages/assets/JSON.

Pre-flight: one task, no shared interfaces between tasks.

Validation before publishing: 71 tests passed. Export: 203 HTML pages, 350 JSON files, 16.4MB. All internal links and CSS font references resolve. Browser confirmed program/discipline navigation, level filter, menu and persisted theme. Source database is accessed via SQLite read-only backup; no worker/lifespan is started.

Final review: independent reviewer found no Critical, Important or Minor issues. Browser validation performed by primary agent.
