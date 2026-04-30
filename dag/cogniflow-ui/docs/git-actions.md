# Releasing via GitHub Actions

Walkthrough of how the two CI workflows under `.github/workflows/` turn a
`git tag` push into downloadable Windows and macOS builds on a GitHub
Release page.

This document is for whoever is shipping a release. For how to *build
locally* without GitHub, see [`build.md`](build.md). For how the
running app behaves at startup, see [`instruction.md`](instruction.md).

---

## 1. The mental model

GitHub Actions is a free build server that lives **inside** your GitHub
repository. The two YAML files under `.github/workflows/` are recipes
that GitHub reads automatically. Each recipe says:

> "when this thing happens in the repo, spin up a machine, run these
> commands, and save the output."

Think of them as robot builders that GitHub keeps on standby. They wake
up only when you give them a signal.

## 2. What a "tag" actually is

A git tag is just a label you stick on a specific commit. `v1.0.0` is a
conventional name meaning "this commit is release 1.0.0". The label
gets pushed to the remote alongside your code:

```
git tag v1.0.0          # stick a label on the current commit (locally)
git push origin v1.0.0  # tell GitHub about the label
```

Both workflows are configured to react to **pushed tags that match
`v*.*.*`** (lines 8–9 of `build-windows.yml` and `build-macos.yml`):

```yaml
on:
  push:
    tags:
      - "v*.*.*"
```

This is the signal that wakes up the robots.

## 3. What happens when you push a `v1.0.0` tag

The moment GitHub sees the new tag arrive, it triggers **both**
workflows in parallel:

```
                  git push origin v1.0.0
                          |
                          v
              +----------------------+
              |  GitHub sees new tag |
              +----------+-----------+
                         |
              +----------+-----------+
              v                      v
     [windows-latest VM]      [macos-14 VM]
     spawned by GitHub        spawned by GitHub
              |                      |
        clone the repo          clone the repo
        install Python          install Python
        pip install -r ...      pip install -r ...
        pyinstaller spec        pyinstaller spec
        zip the build           zip the .app (ditto)
              |                      |
              v                      v
       Cogniflow-UI-           Cogniflow-UI-
       Windows.zip             macOS.zip
              |                      |
              +----------+-----------+
                         v
              GitHub Release page for v1.0.0
              with both zips attached for download
```

The two builds run on **separate physical machines** — a real Windows
machine and a real Apple-silicon Mac, both inside GitHub's data centers.
Each takes roughly 3–8 minutes. They do not interfere with each other.

## 4. What students actually see at the end

GitHub creates a page like this automatically:

```
https://github.com/<your-username>/<your-repo>/releases/tag/v1.0.0

Cogniflow UI v1.0.0
-----------------------------------------
Assets:
  [zip] Cogniflow-UI-Windows.zip   35 MB
  [zip] Cogniflow-UI-macOS.zip     42 MB
  [src] Source code (zip)
  [src] Source code (tar.gz)
```

Students click the zip for their OS, unzip it, double-click the
launcher, and the browser opens to the app.

## 5. Concrete prerequisites

The `dag/cogniflow-ui/` folder needs to be a git repository hosted on
GitHub before any of this works. One-time setup:

### 5.1 Create a repo on github.com

Either public (free, unlimited CI minutes) or private (free with limits:
2000 minutes/month, plenty for this project).

### 5.2 Initialize the local folder and push it

From inside `dag/cogniflow-ui/`:

```
git init
git add .
git commit -m "Initial Cogniflow UI v1"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

The `.gitignore` in this folder already excludes `venv/`, `build/`,
`dist/`, and Python caches, so none of that gets pushed.

### 5.3 Verify the workflows are live

Open the repo on github.com, click the **Actions** tab. You should see
the two workflows listed:

* "Build Windows .exe"
* "Build macOS .app"

They will not have run yet because no tag has been pushed and no manual
trigger has happened.

## 6. The release flow, end to end

After the one-time setup, every release looks like this:

```
# 1. Update the version in dag/cogniflow-ui/config.json (e.g. 1.0.0 -> 1.1.0)
# 2. Edit any seed_pipelines/ as needed
# 3. Commit your changes

git add config.json seed_pipelines/
git commit -m "Bump to v1.1.0 - updated writer prompt"
git push

# 4. Tag the commit and push the tag

git tag v1.1.0
git push origin v1.1.0

# 5. Wait roughly 5 minutes, then check:
#    https://github.com/<you>/<repo>/releases
```

When the workflows finish, both zips appear on the v1.1.0 release page
automatically. You can share that one URL with your students; each one
clicks the platform that matches their OS.

Each subsequent release is the same three commands: bump version,
commit-push, tag-push.

## 7. Manual builds without tagging

You do not have to push a tag to test a build. Both workflows also have
`workflow_dispatch:` enabled, which adds a "Run workflow" button on the
Actions tab.

Clicking it runs the build on the currently selected branch and uploads
the zip as a **workflow artifact** — downloadable from the run page,
but not attached to a Release. This is useful for sanity-checking that
your spec still works after edits, before committing to a real release.

## 8. The `dist/`, `build/`, and `venv/` folders never go to GitHub

They are listed in `.gitignore`, so even if you `git add .` they will be
skipped. Only the source code is pushed; GitHub's runners build their
own copy from scratch on each run.

## 9. Why this works for non-technical students

The whole reason for this pipeline is so that students do **not** need
to know how to build software. From their perspective:

1. You hand them a single URL: the GitHub Releases page for the current
   version of the course.
2. They click the zip that matches their OS.
3. They unzip it.
4. They double-click the launcher (`cogniflow-ui.exe` on Windows, the
   `.app` on macOS).
5. The browser opens, they see the home page with the bundled pipelines.

No Python, no `pip install`, no terminal, no orchestrator-folder hunt
(the `config.json` next to the launcher tells the UI where the
orchestrator lives — students may need to edit that one line if they
installed the orchestrator in a non-default location).

## 10. Code-signing the macOS .app (optional, future)

By default the macOS `.app` is unsigned. Students see Gatekeeper's
"developer cannot be verified" warning on first launch and must
right-click → Open. Details and the workaround instructions to give
students live in [`build.md`](build.md) §6.

If you want to eliminate the warning entirely, you need an Apple
Developer ID ($99/year) and a notarization step. The TODO comment at
the top of `build-macos.yml` lists exactly which secrets to configure
on the GitHub repo (Settings → Secrets and variables → Actions):

* `APPLE_ID` — your Apple Developer account email
* `APPLE_TEAM_ID` — your Apple Developer team identifier
* `APPLE_APP_PASSWORD` — an app-specific password generated from
  appleid.apple.com

Once those are set, add `codesign` and `xcrun notarytool submit` steps
between the PyInstaller build and the upload. The signed-and-notarized
`.app` then opens with no warnings.

## 11. Cost

Both workflows run on free-tier minutes for public repositories
(unlimited) and for private repositories (2000 minutes/month included
with a free GitHub account). A typical release uses roughly 10–15
minutes total across both runners, so you can ship dozens of releases
per month at zero cost.

## 12. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Pushed a `v1.0.0` tag, nothing happened | The workflow file was added to the repo *after* the tag, or the tag was pushed without the workflow file present in that commit. | Make sure `.github/workflows/*.yml` is part of the same branch the tag points at. Re-tag if needed. |
| Workflow runs, but Release page is empty | The `softprops/action-gh-release` step ran but the repo's permissions are too restrictive. | Repo Settings → Actions → General → Workflow permissions → set to "Read and write permissions". |
| Build fails on the Mac runner with "no module named …" | A Python module that PyInstaller missed in static analysis. | Add it to `hiddenimports` in `cogniflow-ui.spec` and re-tag. |
| Build succeeds, students still see old behavior after upgrading | They have the new build but the same `app_version` as before, so the seeder skipped on launch. | Bump `app_version` in `config.json` whenever you ship updated bundled pipelines (see `instruction.md` §1.4). |
| Build is fast but the `.app` zip is missing on macOS | `ditto` was not used; macOS strips the executable bit when zipping with the standard `Compress` action. | The provided workflow already uses `ditto`. If you replace it, use `ditto -c -k --sequesterRsrc --keepParent`. |
