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

## Current capabilities

- **CSV structure repair** — restores records split by unquoted line breaks,
  detects delimiters and encodings, removes invalid leading rows, and exports a
  one-record-per-line CSV or Excel file.
- **English and Polish numbers** — recognizes `1,234.56`, `1 234,56`, and
  `1.234,56` safely while preserving decimal precision and Excel-safe large
  values.
- **Promotion time-series normalization** — validates an Excel template with
  `Promotion_Master` and `Support_Rules`, keeps source rules compact, and
  exports inclusive daily support rows without summing overlapping rules.
- **Clear, localized workflow** — CSV repair and promotion normalization are
  separate tabs, with English, Korean, and Polish user interfaces.
- **Fast per-user installation** — runs from Local AppData in an `onedir`
  layout, so the launcher does not unpack a single-file bundle on every start.
- **Update notification** — checks GitHub for a newer stable release in the
  background, with a 24-hour cache and a user-controlled toggle.

## Data model direction

Promotion rules remain the source of truth in a compact period-based table.
The daily support file is an analytical output, not a replacement for the
source rules. Future normalizers, such as a price-history template, should use
the same pattern: preserve compact source facts and generate time-series data
only when it is needed for analysis.

## Download and run

Download the single setup file from the latest release. It installs the app
only for the current Windows user; Python and extra libraries are not required.

👉 **[Download the latest installer](https://github.com/KwangBeomPark/DataRefinery/releases/latest)**

1. Download `App04_DataRefinery_Setup_v<version>.exe`.
2. Run the installer. It creates **Data Refinery** shortcuts in the Start menu
   and on the desktop.
3. Use **CSV repair** for malformed delimited files, or **Promotion template**
   for normalized promotion rules and daily support data.
4. Results are saved beside the source data with a `YYYYMMDD_HHMM` timestamp.

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
