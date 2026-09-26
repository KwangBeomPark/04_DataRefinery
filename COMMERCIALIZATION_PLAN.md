# Data Refinery commercialization plan

Target: a supportable Windows desktop release for non-technical users who repair CSV files, normalize promotion rules, and aggregate datasets. The current 1.11.0 baseline has 354 passing tests and signed release binaries. Release decisions use the gates below, not a date alone.

| Stage | Work | Exit gate |
| --- | --- | --- |
| 1. Release safety | Pin dependencies, run Windows CI, require valid signing, validate launcher downloads, publish SHA-256 checksums | Tests and installer smoke check pass; the release build fails if signing fails; the launcher rejects an untrusted installer |
| 2. Product verification | Test packaged application flows, large files, malformed inputs, read-only/disk-full failures, Windows display scaling, keyboard access | CSV, promotion, and aggregation journeys work on a clean Windows 10/11 machine; no P1 defects remain |
| 3. Supportability | Add local diagnostic logging, error identifiers, clear recovery messages, and a support route | A user can report version and error ID without sharing source data |
| 4. Onboarding | First-run guidance, sample-based walkthrough, consistent result and recovery screens | New users can finish a sample task and find its output without help |
| 5. Commercial release | Publish product terms, privacy/data-handling notice, support policy, security contact, change log; run a 5–10 person beta | Beta blockers resolved; upgrade and rollback rehearsed; release candidate approved |

Stage 1 is being implemented first because later testing and beta results need a trustworthy build. Stage 2 requires manual checks on real desktop sessions in addition to automated tests. Product terms, support commitments, and data policies require the owner's business decisions before publication.

## Progress as of 2026-09-26

- Implemented: pinned Windows/Python dependencies, isolated installer builds, Windows CI, install/uninstall smoke script, signing failure gate, installer checksum, launcher signer verification, canonical release URL, local error IDs/logs, and public bug-report instructions.
- Verified locally: 358 automated tests, focused Ruff checks, the unsigned isolated test build, release lookup, and signer verification against an existing signed installer.
- Verified in CI: the Windows workflow passed its first run, including package creation and isolated install/uninstall.
- Pending verification: a clean-machine packaged-app walkthrough with real user actions and display scaling. The current machine can verify signatures but could not access the release certificate's private key to create a new signature (`Bad UID`).
- Before paid production: resolve signing-key access, obtain the applicable Inno Setup commercial license, define product terms/privacy/support commitments, and run a small user beta. The [Inno Setup licensing guidance](https://jrsoftware.org/isorder.php) says commercial users should purchase a license and includes CI/compiler use in its licensing model.

## Release procedure

1. Use Python 3.13 on Windows. The release script creates a fresh environment from `requirements.txt`.
2. Run `ruff check --select E4,E7,E9,F src tests` and `python -m unittest discover -s tests -q`.
3. Run `scripts/build_release.ps1` with the release signing certificate available. Do not use `-SkipSign` for a published release.
4. Verify the Authenticode status and SHA-256 checksum of both the installer and application executable.
5. Test installation, upgrade, application flows, and uninstallation on a clean Windows machine.
6. Publish the signed installer and its `.sha256` file under a tag matching the application version.
