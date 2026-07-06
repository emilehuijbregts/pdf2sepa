# Client seed data (manual build only)

Do **not** commit real client data to git.

## Build a client-specific installer

1. Copy and adapt `settings.json` and `suppliers.json` for the client on Windows:
   - Set `export_dir` to a Windows path (e.g. `%USERPROFILE%\Documents\PDF2SEPA\exports`)
   - Remove Mac paths (`/Users/...`)
2. Place files in a local folder, e.g. `C:\build\seed\`
3. Build the PyInstaller app and standard installer first (or use CI artifacts)
4. Compile the client installer:

```bat
iscc packaging\installer-client.iss /DMyAppVersion=1.0.1 /DSEEDDIR=C:\build\seed
```

Output: `packaging\output\PDF2SEPA-Setup-1.0.1.exe` with seeded data on fresh install only.

Seed files are **not** included in auto-update zips.
