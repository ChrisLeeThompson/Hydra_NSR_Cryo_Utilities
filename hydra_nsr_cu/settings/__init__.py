"""Persistent application settings.

The :class:`SettingsController` exposes user preferences to QML and persists
them via ``QSettings``. Factory defaults live in :mod:`defaults` so the
controller has a single, self-contained source of fallback values when a key
is missing from the user's settings store.
"""