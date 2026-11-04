# Local display corrections

User requested local changes only. Do not push or update GitHub Pages.

- [x] Add failing regressions for practice labels/wrapped values, presentation-only professional area codes, Web OCR label, exact clock hours/week rounding and absence of JSON export links.
- [x] Fix label-bounded practice extraction; reuse saved source section for legacy display; leave absent values absent.
- [x] Normalize the evidenced Web OCR variant for parsing and display; remove only professional area codes from display and repair observed wrapped words.
- [x] Show ordinary hours/minutes with semester pairs (one pair = 90 minutes); calculate weekly averages before rounding.
- [x] Remove JSON buttons/download generation from future static exports; preserve internal robot JSON storage/API.
- [x] Run full tests, rebuild cached pages locally, rebuild/restart Docker, verify affected pages and browser layout. No remote publishing.

Review: fixed dehyphenation of real compounds/coordination, preservation of structured fields when source labels are absent, and labels wrapped before separators. Each has a failing regression before its fix. User clarified conversion must use ordinary hours: 140 pairs = 210 hours.

Final validation: 81 tests passed. Four saved program contexts rebuilt after SQLite backup. Running Docker HTTP confirms three programs, corrected Web name, clean areas, practice84 whole type with no empty labels, and all curriculum links working. Ordinary time: AI140pairs=210h, weekly10pairs=15h; 148pairs/14weeks yields15h51m. Local-only static export203HTML, zero document JSON downloads. No commits, pushes or GitHub Pages changes in this task.
