# Data Refinery release checklist

This project publishes one per-user Windows setup executable. The installer
places the PyInstaller `onedir` application under the current user's Local
AppData.

## Required executable naming rule

Every release executable **must begin with `App04_`**.

The PyInstaller specification derives the installed application bundle name
from `__version__` in `src/data_refinery.py`:

```text
App04_DataRefinery_v<version>.exe
```

The installer is the only GitHub release asset and must use this format:

```text
App04_DataRefinery_Setup_v<version>.exe
```

For example, version `1.6.0` is released as
`App04_DataRefinery_Setup_v1.6.0.exe`. Do not upload an executable that does
not follow this prefix rule.

## Release steps

1. Update `__version__` in `src/data_refinery.py`.
2. Update the release notes and both README files when necessary.
3. Run `python -m unittest discover -s tests -v`.
4. Run `.\scripts\build_release.ps1` with Inno Setup 6.7+ or 7 installed.
5. Verify `release/dist/App04_DataRefinery_v<version>/App04_DataRefinery_v<version>.exe` exists.
6. Verify `release/dist/installer/App04_DataRefinery_Setup_v<version>.exe` installs to
   `%LOCALAPPDATA%\Programs\Data Refinery` without an administrator prompt.
7. Create Git tag `v<version>` and upload only that setup executable to the
   matching GitHub release.

## Rename migration

`v1.6.0` changes the product name from CSV Modifier to Data Refinery. The
installer keeps the same application identity so it can replace the old app,
removes only obsolete application files and shortcuts, and preserves user
output files. Update preferences migrate from `%LOCALAPPDATA%\CSV Modifier` to
`%LOCALAPPDATA%\Data Refinery` on the next settings save.
