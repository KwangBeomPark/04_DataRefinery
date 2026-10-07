# Data Refinery build and signed release

The source version is defined only in `src/version.py`. The application uses
PySide6, Python 3.13, PyInstaller onedir, and Inno Setup 6.7+ or 7.
This layout follows the sibling ClipOCR PL Suite release standard. The Stepwise
interactive SimplySign workflow is retained with explicit verification and
immutable published versions.

## Locations and files

- `installer/setup.iss`: installer source. AppId remains
  `{2E1A7E3F-8D78-4DB0-9B62-50B12CD4326F}`.
- `installer/*.spec`: application and launcher PyInstaller definitions.
- `build/`: disposable pinned environment, compilation work and build provenance.
- `dist/`: unsigned onedir app, launcher and installer previews.
- `tools/release-history/`: private local signing logs, verified signed components,
  prior official files and preservation maps; never committed or uploaded wholesale.
- `docs/release-notes/RELEASE_NOTES_v<version>.md`: authored version notes.
- `release/`: only the latest signed official installer names, launcher, their
  `.sha256` sidecars, `SHA256SUMS.txt`, `build-manifest.json`, and copied latest note.

The installer names are `App04_DataRefinery_Setup_v<version>.exe` and
`DataRefinery-Setup.v<version>.exe`. They contain exactly the same signed bytes.
On NTFS, a hard link avoids storing a second copy locally. On other filesystems,
the script copies and verifies instead; this increases disk usage. Uploads or
copies to another filesystem may also store both complete files.

The main EXE requires its `_internal` dependencies. It is not distributed alone
as a portable program. The one-file `App04_DataRefinery_Launcher.exe` remains an
optional installation/update entry point with the existing trusted publisher.

## Build, sign and publish

1. Bump `src/version.py` for a new release. Never repoint a released version/tag
   to another source commit. Create its note in `docs/release-notes/`.
2. Commit the intended source on `main`, then run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts/build.ps1
   ```

   Both app and launcher use the same fresh environment from `requirements.txt`.
   Tests use isolated AppData. An unsigned installer preview and both naming
   aliases are staged in `dist/staging`. The previous official `release` is
   untouched. `-SkipTests` is for unsigned CI previews; such build records cannot
   be used by the signer. Starting a build invalidates the old provenance record.
3. Keep SimplySign Desktop logged in. In a user-opened Administrator PowerShell:

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts/sign.ps1
   ```

   SignTool is found through `SIGNTOOL_PATH`, `tools/signtool/signtool.exe`, PATH,
   or Windows SDK. Its Microsoft signature must be valid. Smart Card service
   `SCardSvr` must start. `CertPropSvc` and `ScDeviceEnum` are attempted too.
   Passwords, PINs and OTPs remain user-controlled.
4. The signer checks clean main, version, exact commit and bundle hashes. It
   signs working copies so a failure does not alter the unsigned build inputs.
   Inno calls the same `sign.ps1` internally to sign installer and uninstaller.
   The temporary signed uninstaller is saved before Inno deletes it.
5. Every required binary must have the expected publisher, a timestamp, and a
   successful `signtool verify /pa /all`. Only after all checks pass is the new
   official directory promoted; the prior one is archived, with rollback if the
   directory promotion fails.
6. To also push source, wait for the exact-commit Windows release check, tag and
   upload a new version, use `scripts/sign.ps1 -Publish`. The initial upload is a
   draft; it is published only after uploaded digests match. Already tagged
   versions cannot be resigned. Upload errors are failures, not success messages.
7. After an interrupted upload, use `scripts/sign.ps1 -PublishOnly` without
   accessing the signing key. It requires the same clean source commit and
   verified existing official artifacts, reuses matching tags/drafts, uploads
   missing files only and refuses changed existing remote files. It never uses
   `--clobber`. Result records distinguish whether local promotion occurred.
   Draft verification resolves the release ID through authenticated `gh release
   view`; it does not use the published-only tag endpoint. A failed upload with
   a `starter` residue requires manually removing that failed asset before retry.

Checksums use UTF-8 without BOM and cover every official file except
`SHA256SUMS.txt` itself, including manifest and notes. The manifest records
version, source commit, build/signing times, aliases, signatures and component
hashes. `.sha256` sidecars retain compatibility with previous deployment tooling.

## Verification and storage preservation

```powershell
powershell -ExecutionPolicy Bypass -File scripts/sign.ps1 -VerifyOnly
powershell -ExecutionPolicy Bypass -File tests/test_release_signing.ps1
```

`-VerifyOnly` does not sign, publish, start services or require an administrator.
Fault-injection tests do not access private keys. CI additionally builds and
installs/uninstalls the unsigned preview on an ephemeral Windows runner.
It does not prove live SimplySign key access or manual GUI/Excel behavior.

Installation uses `%LOCALAPPDATA%\Programs\Data Refinery`; the wizard does not
offer a different directory. `/DIR` is used only for isolated CI installer tests.
Its `UserSetting` stores configuration, logs, presets and `datasets` workspaces.
The installer excludes bundled UserSetting data and preserves the directory on
uninstall. Legacy application directories are not recursively deleted.
Runtime one-time migration copies and hashes old data, preserves originals,
and never overwrites existing new settings.

Local cleanup is separate from signing. Preview `scripts/clean_local_artifacts.ps1`
before running it with `-Apply`. It refuses linked paths, user data and unarchived
signed output. It never targets AppData, shared data folders or certificate stores.
