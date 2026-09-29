*Read this in other languages: [English](README.md), [한국어](README.ko.md)*

# Data Refinery

![Data Refinery workflow: source files are repaired and normalized into analysis-ready data](assets/images/manual-data-refinery.png)

> **Clean, normalize, and prepare data for analysis.**

Data Refinery is a Windows desktop application for non-technical users who need
to turn difficult source files into reliable, analysis-ready data. It repairs
malformed CSV records, keeps promotional rules in a compact normalized model,
and produces a daily time-series file when analysis requires it.

The application is deliberately designed as a home for additional data
normalizers. Pricing and other business-data templates can be added without
mixing their source rules into the promotion model.

## Current capabilities (v1.11.1)

- **CSV structure repair & malformed row safety** — restores records split by
  unquoted line breaks, detects delimiters and encodings, removes invalid leading
  rows, rejects overwide rows exceeding the expected column count with clear row-number
  diagnostics without creating partial output files, and exports clean CSV/Excel files.
- **English and Polish numbers** — recognizes `1,234.56`, `1 234,56`, and
  `1.234,56` safely while preserving decimal precision and Excel-safe large
  values.
- **Promotion time-series normalization & failure handling** — validates an Excel
  template with `Promotion_Master` and `Support_Rules`, rejects templates with duplicate
  non-empty column headers, preserves results across tab navigation, and uses staged
  writes with compensating cleanup to remove already-published outputs if a later publish step
  fails (this is not an all-files crash-atomic transaction and does not restore pre-existing files).
- **Data aggregation with failure handling** — groups CSV rows, applies filters and
  per-column aggregation functions, calculates derived measures, validates input
  rows for overwide columns prior to aggregation, provides clear sample-only preview
  labeling, attempts cleanup of temporary files across normal cancellation or save-failure
  paths (without guaranteeing cleanup if removal itself fails), and saves reusable presets.
- **Keyboard-accessible field lists** — field lists in data aggregation support full
  keyboard focus with visible accent focus rings, automatic first-row focus initialization,
  continuous Return/Delete placement and removal, and selection preservation across
  rerenders.
- **Clear, localized workflow** — CSV repair, promotion normalization, and
  aggregation have separate tabs, with English, Korean, and Polish interfaces.
- **Fast per-user installation** — runs from Local AppData in an `onedir`
  layout, so the launcher does not unpack a single-file bundle on every start.
- **Update notification** — checks GitHub for a newer stable release in the
  background, with a 24-hour cache and a user-controlled toggle.

## Quality and verification status

- **Automated test suite & CI**: 440 automated tests pass locally across CSV processing,
  promotion normalization, data aggregation, keyboard navigation, and failure-handling paths,
  alongside CI-selected Ruff lint checks. Windows CI validates linting, tests, builds an unsigned test
  installer and launcher, and runs a silent install/uninstall smoke test on an ephemeral runner
  (this is not release signature or checksum verification).
- **Manual QA & readiness caveats**: While automated mapped-widget interactions and core
  data pipelines are verified by tests, full installed-application keyboard-only workflows,
  high-DPI display scaling (125%/150%), and end-to-end multi-language UI layout checks
  remain unverified pending manual desktop QA. This release is a desktop quality hardening
  update and is not a commercial product launch.

## Data model direction

Promotion rules remain the source of truth in a compact period-based table.
The daily support file is an analytical output, not a replacement for the
source rules. Future normalizers, such as a price-history template, should use
the same pattern: preserve compact source facts and generate time-series data
only when it is needed for analysis.

## Download and run

Download the single setup file from the latest release. It installs the app
only for the current Windows user; Python and extra libraries are not required.

👉 **[Download the latest installer](https://github.com/KwangBeomPark/04_DataRefinery/releases/latest)**

1. Download `App04_DataRefinery_Setup_v1.11.1.exe`.
2. Run the installer. It creates **Data Refinery** shortcuts in the Start menu
   and on the desktop.
3. Use **CSV repair** for malformed delimited files, **Promotion template**
   for promotion rules and daily support data, or **Data aggregator** to group and summarize a CSV file.
4. Results are saved beside the source data with a `YYYYMMDD_HHMM` timestamp.

To try aggregation without your own data, open `sample_data/monthly_ledger_sample.csv`
in the **Data aggregator** tab. Add `디비전` as a row group and `매출` as a value,
preview the result, then save it as CSV or Excel.

## Development

The repository keeps application code in `src`, bundled files in `assets`,
build scripts in `scripts`, and installer configuration plus generated release
output in `release`.

Run the desktop app during development with:

```powershell
python -m src.data_refinery
```

Run the test suite with:

```powershell
python -m unittest discover -s tests -v
```

Build the Windows installer with:

```powershell
.\scripts\build_release.ps1
```

If a processing error appears, include its error ID in a [bug report](SUPPORT.md). Diagnostic logs remain on the local PC and do not include source file contents.
