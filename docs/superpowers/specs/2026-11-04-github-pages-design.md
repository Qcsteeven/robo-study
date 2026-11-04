# GitHub Pages: просмотр результатов

User selected publication of saved results on GitHub Pages, without running the robot.

Publish a static snapshot of the existing catalog, program and discipline pages from the local SQLite database. Preserve styles, semester controls, calendar, menu, filters and theme. Link to original university PDFs and export available latest primary/final JSON files. Clearly label the snapshot and explain that refresh/OCR requires the local server. No database, credentials, logs or administrative API is published.

Approaches: a server deployment would support robot runs but is outside the chosen GitHub Pages scope; a static snapshot supports the selected review use case and is chosen by the user. Rewriting the app as a client application would add unnecessary scope.

Implementation choice: a Python exporter uses existing read-only page routes against a temporary SQLite backup, rewrites links for /robo-study/, and validates local destinations. Generated files live on gh-pages, source remains on the current branch. Pages uses gh-pages root with .nojekyll. Updates are explicit exports, not automatic crawling of the university.
