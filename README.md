Hydra NSR Cryo Utilities 3

Run in simulation mode:
1. Edit the defaults.py file located in Hydra_NSR_Cryo_Utilities_3.0/hydra_nsr_cu/defaults.py
2. At the bottom of the file, set "DEV_FORCE_SIMULATION" to True
3. Run the main module, "hydra_nsr_cryo_utilities.py"
4. The UI will open in simulation mode without microscope connection. Note you cannot control the microscope in this mode.

Updating the app:
1. Download the latest version from GitHub (Code > Download ZIP, or git pull).
2. Copy the new files over your existing app folder, overwriting when prompted. Do not delete the old folder first.
3. Your data is preserved by an update:
   - Saved templates in templates/ are never overwritten. The shipped default template lives inside the app (hydra_nsr_cu/cryo/factory_templates/) and is only copied into templates/ at startup when a file with that name is missing.
   - The session log (session_logs/session_log.jsonl) is not part of the download, so it is never touched.
   - Settings, stage positions, and the activity list are stored in the Windows registry, outside the folder entirely.
4. What is replaced: the application code, including the factory templates inside hydra_nsr_cu/.

Notes:
- If you delete templates/default.json it reappears on the next launch (reseeded from the factory copy). To customize the default, edit it and save over it - your version is always kept.
