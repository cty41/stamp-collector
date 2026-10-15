# Stamp Collection Offline

A small Python tool for keeping a local stamp archive and generating a read-only
HTML catalog. SQLite stores records and review history; images stay in files.
Repeated imports reuse identical photos. Analysis results require review before
changing official records.

## Try the sample

Open [sample/index.html](sample/index.html) in your browser, or double-click
`open_sample.cmd` on Windows. No server, Python installation or internet needed.
It contains 12 groups and 21 observation previews from one collection. Copy the
whole `sample` folder, not just its HTML file. Stamp names and the UI are Chinese.

Same-design records share a display group; expand it to see each member.
Sorting defaults to fewer visible examples first, then collecting interest.
Counts may include repeated photographs: they are not inventory or market rarity.

## Run locally

Requires Python 3.10+.

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python collection.py init
python collection.py import photo.jpg --batch my-stamps
python generate_offline.py
```

Importing a photo alone does not identify or crop stamps. Use `extract --boxes`
to propose crop regions and `review --task` to accept them; see each command's
`--help`. `analyze` prepares research tasks or imports structured candidates; it
does not provide automatic online identification.

Each export creates a new folder. Open the generated `index.html` directly;
`generate_offline.cmd` is the Windows shortcut for generating a new catalog.
Use `--root` to select another archive. Optional `display_groups.json` stores
reviewed grouping and representative choices; without it, records stay separate.
Back up this config alongside the archive.

Local databases, photos, exports and machine settings are ignored by Git.
Only the curated preview sample is committed. Exported personal catalogs can
contain private data; the sample is intentionally stripped of it.

## Optional legacy features

Old server, migration, detection and Excel-export code is retained for backup.
It is not needed for offline browsing. Detection requires `ultralytics` and your
own compatible model. Migration expects the original project's input layout.
Excel requires Node.js, `@oai/artifact-tool` and a local `runtime.json` containing
the `node` executable path; that environment is not bundled. HTML/JSON work
without it. No cloud services or API keys are required by the core tool.

Tests: `python -m unittest discover -s tests -v`.
Optional JavaScript logic checks: `node tests/test_offline_ui.mjs`.

## License

Code: [Apache-2.0](LICENSE). This project grew from work with
[code2k13/philately-tool](https://github.com/code2k13/philately-tool).
No upstream model or third-party runtime is bundled. Sample stamp artwork may
carry third-party rights; the code license does not grant rights to that artwork.
