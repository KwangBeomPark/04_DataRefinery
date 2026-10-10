# Release audit — App04 DataRefinery

Audit date: 2026-10-10. Baseline HEAD: `70eea4bb491b4df3a172aeae556ac1e436d3a9ab`.
This report covers the current uncommitted release changes, independently reviewed by Codex after the Gemini handoff.

## Contract and corrections

Directly uploaded assets are exactly:
- `App04_DataRefinery_Setup_v<version>.exe`
- `build-manifest.json`
- `SHA256SUMS.txt`

`scripts/sign.ps1::Get-MissingReleaseAssets` rejects unexpected/duplicate remote assets, duplicate local names, missing upload state, unfinished uploads, size mismatches and digest mismatches. `Publish-VerifiedRelease` requires the three canonical local names, rejects wrong release identity and prerelease status before mutations, and requires an existing release to have a matching remote tag. A complete public release is verified without mutation; an incomplete public release is preserved and rejected. Draft assets are checked before publication and public assets are checked afterward. No overwrite or clobber recovery is permitted.

`AGENTS.md` now distinguishes the canonical GitHub installer from optional local aliases. No alias is generated or uploaded by this audit.

`installer/setup.iss` was inspected, not changed. Existing code preserves `_internal` during installation and performs obsolete-EXE cleanup only at `ssDone`, after the installed EXE matches the compiled SHA-256. This is source evidence, not a tested installation or rollback guarantee.

## Verification

Windows PowerShell 5.1: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests/test_release_signing.ps1` — **54 assertions passed**. External GitHub, Git and signatures are mocked. Coverage includes existing/new drafts, unexpected/duplicate assets, unfinished/missing upload state, wrong release tag, missing draft flag, prerelease draft rejection, missing remote tag, noncanonical filename casing, and no mutation of an already complete public release.

No real signing, installation, push, tag creation or publication was performed in this audit.

## Remaining gates

- Review and commit the complete changes, select a new version for any new official distribution, and author its release notes. Preserve published v2.0.2 and its bytes/tag.
- For a new release, rebuild and sign in the user's interactive SimplySign session; validate app, launcher, installer and archived uninstaller as required by the pipeline.
- Perform isolated Windows installation, running-old-version upgrade, cancellation/failure and UserSetting/dataset preservation acceptance.
- Publish only the newly approved, verified version. Test success here is not evidence of actual KSP signing or installation acceptance.
