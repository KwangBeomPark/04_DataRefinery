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

## Current capabilities (v2.0.1)

- **Modern PySide6 (Qt) Desktop UI** — Completely transitioned from legacy Tkinter to a
  modern, DPI-aware design system (Primary Blue theme, dark header, modern cards, badges, steppers)
  supporting crisp rendering on high-DPI Windows displays. Retains `--legacy-tk` fallback.
- **Dataset Publisher UI/UX Revolution (3-Step Wizard)**:
  - **Step 1: Smart File Selection & Keyword Chip Filters**: Instantly scans CSV files in the input folder;
    tag-based `+ Include (e.g. PL, sales)` and `- Exclude (e.g. backup)` chip filters with checkbox multi-selection.
    Real-time pre-flight verification of column order and required presence against dataset baseline.
  - **Step 2: 1,000-Row Sample Profiler & Auto-Guess**: Analyzes the first 1,000 rows in the background
    to auto-detect encoding, delimiters, and data types, auto-suggesting roles (Period, Key, Numeric, General)
    without tedious manual typing.
  - **Step 3: Compound Key Collision Detection**: Validates compound key uniqueness against the sample
    in real time, alerting users to duplicate groups before loading.
- **European Number (Polish Comma Decimal) Scale Distortion Fix** — Fully patched the critical bug
  where European decimal values like `1 234,56` or `12,34` were multiplied by 100x during naive comma stripping,
  using robust `make_numeric_sql_expr` regex-based casting.
- **CSV structure repair & malformed row safety** — Restores records split by
  unquoted line breaks, detects delimiters and encodings, removes invalid leading
  rows, rejects overwide rows exceeding the expected column count with clear row-number
  diagnostics without creating partial output files, and exports clean CSV/Excel files.
- **English and Polish numbers** — Recognizes `1,234.56`, `1 234,56`, and
  `1.234,56` safely while preserving decimal precision and Excel-safe large values.
- **Multilingual / Central European encoding & Self-Healing recovery** — Detects
  Central/Eastern European encodings (Windows-1250, ISO-8859-2, CP852) via multi-region
  sampling (e.g. Polish and Czech special characters). Provides manual encoding selection
  in the aggregator UI, and offers a one-click interactive recovery prompt when encoding
  mismatches occur without losing configured rules.
- **Promotion time-series normalization** — Validates Excel templates with `Promotion_Master`
  and `Support_Rules`, preserves results across tab navigation, and generates daily time series.
- **Data aggregation with rich controls** — Groups CSV rows, applies filters and
  per-column aggregation functions, calculates derived measures, and saves reusable presets.
- **Dataset accumulation, review, and publishing** — DuckDB accumulates monthly CSV data,
  replaces revised periods, and inspects period, row-count, and numeric totals before approval.
  Approved snapshots publish to CSV and generate Excel analysis templates with Power Query.
- **Real-time multilingual support** — Instant interface language switching between English, Korean, and Polish.
- **Fast per-user installation** — Runs from `%LOCALAPPDATA%\Programs\Data Refinery` in an `onedir` layout for zero unpack delay.
- **Update notification** — Checks GitHub for new stable releases in the background.

## Quality and verification status

- **Automated test suite & CI**: 528 tests covering the entire Qt and engine pipeline; 527 pass
  and one optional Excel COM test is skipped by default.
  1,000,000-row benchmark completed in 1.379s. Cross-reviewed and verified by Claude Opus 5.5 and Codex Sol 6.1.

## Data model direction

Promotion rules remain the source of truth in a compact period-based table.
The daily support file is an analytical output, not a replacement for the
source rules. Future normalizers, such as a price-history template, should use
the same pattern: preserve compact source facts and generate time-series data
only when it is needed for analysis.

## Download and run

Download the single setup file from the latest release. It installs the app
only for the current Windows user at `%LOCALAPPDATA%\Programs\Data Refinery`;
Python and extra libraries are not required.

👉 **[Download the latest installer](https://github.com/KwangBeomPark/04_DataRefinery/releases/latest)**

1. Download `App04_DataRefinery_Setup_v2.0.1.exe`.
2. Run the installer. It creates **Data Refinery** shortcuts in the Start menu
   and on the desktop.
3. Use **CSV repair** for malformed delimited files, **Promotion template**
   for promotion rules and daily support data, **Data aggregator** to group and summarize a CSV file,
   or **Dataset publisher** to accumulate, review, and publish period data for Excel.
4. Results are saved beside the source data with a `YYYYMMDD_HHMM` timestamp.

The installation folder contains application binaries and bundled libraries.
Settings, logs, and working databases are stored separately:

| Purpose | Location |
| --- | --- |
| Application installation | `%LOCALAPPDATA%\Programs\Data Refinery` |
| Settings, recent configurations, diagnostic logs | `%LOCALAPPDATA%\Programs\Data Refinery\UserSetting` |
| Dataset definitions and local DuckDB working databases | `%LOCALAPPDATA%\Programs\Data Refinery\UserSetting\datasets` |
| Published CSV and connected Excel templates | The shared folder selected by the publisher |

`UserSetting` is the **user configuration and working-data subfolder inside the installation directory**. On first use, legacy settings, logs, presets, and dataset workspaces are copied into
`UserSetting` and verified. Original folders are retained as backups; existing new
settings take precedence. Shared CSV paths and Excel connections are unchanged.
The promotion template can be exported from its tab; its source copy is
`assets/templates/promotion_template.xlsx`.

To try aggregation without your own data, open `sample_data/monthly_ledger_sample.csv`
in the **Data aggregator** tab. Add `디비전` as a row group and `매출` as a value,
preview the result, then save it as CSV or Excel.

## Development

The repository keeps application code in `src`, bundled files in `assets`,
build scripts in `scripts`, installer sources in `installer`, and signed output
in `release`. See [project layout and naming rules](docs/project-structure.md)
and the [public code map](docs/CODE_MAP.md). The sole application version definition
is `src/version.py`; Python modules retain their existing snake_case names.

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
.\scripts\build.ps1
```

Unsigned previews are written to `dist/staging`; `release` contains only the latest
signed official files. In the interactive Administrator PowerShell where
SimplySign is logged in, run `scripts/sign.ps1` to sign and verify, or add
`-Publish` to upload a new version. See [release instructions](docs/releasing.md).

If a processing error appears, include its error ID in a [bug report](SUPPORT.md). Diagnostic logs remain on the local PC and do not include source file contents.


Shared installation, settings, release goals, and current exceptions are documented in [Suite standardization](docs/SUITE_STANDARDIZATION.md).

Installer upgrade safeguards and the remaining signed/runtime checks are recorded in [Phase 2 review](docs/STANDARDIZATION_PHASE2_REVIEW.md).

See the [public code map](docs/CODE_MAP.md), [user-data backup and restore guide](docs/USER_DATA.md), and [phases 3–5 review](docs/STANDARDIZATION_PHASE3_5_REVIEW.md). The default backup includes all UserSetting files, including workspace databases; user-selected external inputs and published files require separate backup. The desktop menu is labelled Updates / About to match its actions.


2026-10-09 source release preparation: version 2.0.2 is not published yet. New builds use only `App04_DataRefinery_Setup_v<version>.exe` plus manifest/checksums. Existing v2.0.1 downloads stay unchanged. See [current release checklist](RELEASE_CHECKLIST.md).
