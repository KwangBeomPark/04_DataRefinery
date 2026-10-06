# Data Refinery release checklist

This project publishes one per-user Windows setup executable. The installer
places the PyInstaller `onedir` application at
`%LOCALAPPDATA%\Programs\Data Refinery`.

This is the installation directory. `%LOCALAPPDATA%\Programs\Data Refinery\UserSetting` stores settings
and logs; `%LOCALAPPDATA%\Programs\Data Refinery\UserSetting\datasets` stores dataset definitions and working databases. Legacy local folders are copied
and verified on first use; originals are preserved as backups. UserSetting is
excluded from bundle installation and retained on uninstall. Do not delete user
data when cleaning build artifacts.
See [project layout and naming rules](../docs/project-structure.md).

## Required executable naming rule

Every release executable **must begin with `App04_`**.

The PyInstaller specification derives the installed application bundle name
from `__version__` in `src/version.py`:

```text
App04_DataRefinery_v<version>.exe
```

The primary installer release asset and must use this format:

```text
App04_DataRefinery_Setup_v<version>.exe
```

For example, version `1.6.0` is released as
`App04_DataRefinery_Setup_v1.6.0.exe`. Do not upload an executable that does
not follow this prefix rule.

## Release steps

1. Update `__version__` in `src/version.py`.
2. Update the release notes and both README files when necessary.
3. Run `python -m unittest discover -s tests -v`.
4. Run `ruff check --select E4,E7,E9,F src tests`.
5. Run `.\scripts\build_release.ps1` with Inno Setup 6.7+ or 7 installed. This creates a fresh Python 3.13 virtual environment from the pinned `requirements.txt`. A missing or invalid release signature aborts the build.
6. Verify the application EXE and installer both have valid Authenticode signatures from the expected publisher. Check the generated `.exe.sha256` file against the installer.
7. Verify `release/dist/installer/App04_DataRefinery_Setup_v<version>.exe` installs to
   `%LOCALAPPDATA%\Programs\Data Refinery` without an administrator prompt.
8. Test upgrade and uninstall on a clean Windows machine, then create Git tag `v<version>` and upload the setup executable and its `.sha256` file to the matching GitHub release.

The one-file launcher, when distributed, must be built with `.\scripts\build_launcher.ps1` and signed. It accepts only an exact release asset URL and installer version, then verifies the downloaded installer's Windows signature against the pinned release certificate. When the certificate changes, update the launcher's trusted thumbprint and ship a new signed launcher before releases use the replacement certificate.

The launcher output is `App04_DataRefinery_Launcher.exe` (corrected from the old
`Luncher` spelling). Published binaries are not renamed by repository cleanup.

## Finalize a signed release

After committing a clean main branch and building the unsigned app and launcher,
run `scripts/finalize_signed_release.ps1` in the interactive Administrator
PowerShell where SimplySign is logged in. Provide Microsoft SignTool on PATH or
through `SIGNTOOL_PATH`. The script checks the build version/commit, signs the app
and launcher, and recompiles with Inno Setup SignTool and SignedUninstaller enabled.
The signing hook preserves Inno's temporary `uninst*.tmp` as an `.exe` before
the compiler deletes it. Each finalization uses a fresh archive directory to
prevent an older signed file from satisfying the check. The script then
verifies app, launcher, installer, and preserved uninstaller signatures and
timestamps. It writes checksums, `SHA256SUMS.txt`, `build-manifest.json`, and a
version-specific success/failure result under `release/build`.

Never publish the unsigned preliminary build. Publish only after signature
verification and the Windows release check have passed for the same commit.

## Rename migration

`v1.6.0` changes the product name from CSV Modifier to Data Refinery. The
installer keeps the same application identity so it can replace the old app,
removes only obsolete application files and shortcuts, and preserves user
output files. Legacy settings from `%LOCALAPPDATA%\CSV Modifier` and `%LOCALAPPDATA%\Data Refinery`,
and dataset workspaces from `%LOCALAPPDATA%\DataRefinery\datasets`, are copied into
`%LOCALAPPDATA%\Programs\Data Refinery\UserSetting` on first use. The copy is verified,
originals remain intact, and a completion marker prevents repeated imports.
