# Support and bug reports

The application installation directory is `%LOCALAPPDATA%\Programs\Data Refinery`.
`%LOCALAPPDATA%\Programs\Data Refinery\UserSetting` is a separate settings/log directory, and
`%LOCALAPPDATA%\Programs\Data Refinery\UserSetting\datasets` stores dataset definitions and working databases. Legacy folders are copied and
verified on first use, with originals retained as backups.
These user data directories are preserved during repository cleanup.

For reproducible defects, open an issue at [Data Refinery Issues](https://github.com/KwangBeomPark/04_DataRefinery/issues). Include the app version, Windows version, the task you were using, the steps to reproduce, and any error ID shown by the app.

The app writes a small local diagnostic log at `%LOCALAPPDATA%\Programs\Data Refinery\UserSetting\logs\errors.log`. It records error IDs, exception types, and code locations. It does not deliberately record source file contents, exception messages, or absolute source paths. Logs stay on your computer unless you decide to share them.

Issues are public. Do not attach confidential source files, personal data, or screenshots containing customer information. Reproduce the problem with synthetic data where possible.

This issue tracker is a technical bug-report channel; response times are not guaranteed.
