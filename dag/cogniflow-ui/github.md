# GitHub operations — observability-masterclass / Cogniflow UI

Operational manual for everything GitHub-related in this project: account
setup, CLI install, repo creation, the day-to-day push/pull cycle,
shipping releases, troubleshooting auth, and managing secrets.

This document covers how to **operate on GitHub**. Adjacent docs cover
the other phases:

* [`readme.md`](readme.md) — high-level orientation to `dag/cogniflow-ui/`.
* [`instruction.md`](instruction.md) — runtime behaviour, seeding, configs.
* [`building.md`](building.md) — local PyInstaller builds.
* [`git-actions.md`](git-actions.md) — what the CI workflows do once
  triggered.

---

## 1. Account and CLI setup

### 1.1 GitHub account

You only need a free account at https://github.com/signup. No paid plan
is required for any operation in this manual: public repos get unlimited
CI minutes; private repos get 2000 free CI minutes per month.

### 1.2 Install the GitHub CLI (`gh`)

The `gh` CLI is GitHub's official command-line tool. It handles
authentication via your browser (OAuth device flow), so you never type
or paste a token into a terminal.

**Windows (any of these):**

```
winget install --id GitHub.cli
# or
choco install gh
```

**macOS:**

```
brew install gh
```

**Verify:**

```
gh --version
```

You should see something like `gh version 2.x.x`.

### 1.3 Authenticate once

```
gh auth login
```

The CLI will ask:

* **Where?** GitHub.com (not GitHub Enterprise).
* **Protocol?** HTTPS (simpler than SSH for new users).
* **Authenticate Git?** Yes (so plain `git push` also works).
* **How?** Login with a web browser. The CLI prints an 8-character code
  and opens your browser to https://github.com/login/device. Paste the
  code, click Authorize. Done.

The credential is now stored securely in your OS credential manager
(Windows Credential Manager / macOS Keychain). You will not be prompted
again from this machine. Verify:

```
gh auth status
```

Should print `Logged in to github.com as <your-username>`.

### 1.4 Set git identity (one-time, if not already set)

```
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

Use the same email associated with your GitHub account so commits show
up under your profile.

---

## 2. Creating the repository

### 2.1 Decide: public or private

| | Public | Private |
|---|---|---|
| Anyone can view source | yes | no, only people you invite |
| Anyone can download Releases | yes | no, GitHub login + access required |
| Free CI minutes | unlimited | 2000 / month |
| Best for | courses, open educational material | internal-only material |

For an observability *masterclass* aimed at students, **public** is
almost certainly the right answer: students don't need GitHub accounts
to download binaries, and you get unlimited CI builds.

### 2.2 Recommended repo name

```
observability-masterclass
```

The natural URL students will share:
`github.com/<you>/observability-masterclass`.

### 2.3 Create the repo (one command)

From the **repo root** (the `observability-masterclass/` folder that
contains `dag/` and `cyclic/` — two levels above this UI):

```
gh repo create observability-masterclass --public --source=. --remote=origin
```

What that does:

* Creates `https://github.com/<you>/observability-masterclass` on
  GitHub, empty.
* Adds it as the `origin` remote in your local git config.
* Does NOT push yet — gives you a chance to review what you're about to
  upload.

Check the configured remote:

```
git remote -v
```

Should print two lines pointing at the new repo URL.

### 2.4 First commit and push

```
git init                    # if not already a git repo
git add .
git commit -m "Initial commit - DAG UI + orchestrator, cyclic orchestrator"
git branch -M main
git push -u origin main
```

The `.gitignore` at the repo root excludes `venv/`, `build/`, `dist/`,
caches, `.env`, and editor noise — those will not be uploaded.

### 2.5 Verify on github.com

Open `https://github.com/<you>/observability-masterclass` in a browser.
You should see the source tree, with the top-level `readme.md` rendered
on the main page. Click the **Actions** tab. You should see the two
workflow definitions: "Build DAG UI - Windows" and "Build DAG UI -
macOS". They have not run yet because no tag has been pushed.

---

## 3. Repository layout

The repo is organized by **execution flavor** at the top level so each
flavor is a self-contained reference implementation:

```
observability-masterclass/                     <- repo root
├── readme.md                                  <- entry point for the whole repo
├── .github/workflows/
│   ├── build-dag-windows.yml
│   └── build-dag-macos.yml
├── dag/
│   ├── readme.md
│   ├── cogniflow-ui/                          <- you are here, when reading these docs
│   └── cogniflow-orchestrator/                <- DAG orchestrator (v1)
└── cyclic/
    ├── readme.md
    ├── cogniflow-ui/                          <- placeholder, UI in development
    └── cogniflow-orchestrator/                <- cyclic orchestrator (v3.5)
```

Why flavor-at-top:

* Each flavor is a self-contained "lab kit" — students cloning the repo
  see DAG and cyclic side-by-side, not hidden behind a `git checkout`.
* Tag conventions are scoped per flavor (`dag-v1.0.0`, `cyclic-v1.0.0`),
  so the two flavors version independently.
* Diffs between `dag/` and `cyclic/` are git diffs — the educational
  comparison is at filesystem level.

Workflows live at the repo root (`<repo-root>/.github/workflows/`) and
each one declares `working-directory: dag/cogniflow-ui` (or
`cyclic/cogniflow-ui` once that exists) at the job level so the build
runs in the right subfolder.

---

## 4. Daily workflow

### 4.1 Make a change locally

Edit files. Then:

```
git status              # see what changed
git diff                # see the actual edits
git add <files>         # stage what you want to commit
git commit -m "Short description"
git push
```

`git push` (with no extra args) pushes the current branch to its remote
counterpart. After the first `-u origin main` push, no extra flags
needed.

### 4.2 Pull the latest changes

If you work from multiple machines or anyone else collaborates:

```
git pull
```

### 4.3 Create a topic branch (recommended for non-trivial changes)

```
git checkout -b feature/new-pipeline-template
# ...edit, commit...
git push -u origin feature/new-pipeline-template
```

Then open a pull request:

```
gh pr create --title "Add new pipeline template" --body "Adds 16-data-pipeline as a bundled seed."
```

Merge it from the website or with `gh pr merge`.

---

## 5. Shipping a release

This is the operation students actually care about.

### 5.1 Decide what's in the release

Either:

* No content changes, just bumping for a routine cycle (rare).
* Updated `seed_pipelines/<name>/` files.
* New bundled pipelines added under `seed_pipelines/`.
* Code changes (UI features, bug fixes).

### 5.2 Bump the version

Edit `dag/cogniflow-ui/config.json` and bump `app_version`:

```json
"app_version": "1.1.0",
```

Versioning rule of thumb (semver):

* `MAJOR.MINOR.PATCH`
* Bump **PATCH** for bug fixes (1.0.0 → 1.0.1).
* Bump **MINOR** for new bundled pipelines or new features (1.0.0 → 1.1.0).
* Bump **MAJOR** for breaking changes to config or pipeline format
  (1.0.0 → 2.0.0).

### 5.3 Commit and tag

Run from the repo root:

```
git add dag/cogniflow-ui/config.json dag/cogniflow-ui/seed_pipelines/
git commit -m "DAG UI v1.1.0 - add 16-data-pipeline, refresh writer prompt"
git push

git tag dag-v1.1.0
git push origin dag-v1.1.0
```

The tag push is the trigger. Both DAG CI workflows start within seconds.
(The `dag-` prefix scopes the trigger — cyclic releases will use
`cyclic-vX.Y.Z` tags and reach their own workflows.)

### 5.4 Watch the build

```
gh run watch
```

Picks the most recent run and streams the live status. You'll see both
"Build Windows .exe" and "Build macOS .app" progress through their
steps. Build time is typically 4–8 minutes per platform; they run in
parallel.

To list recent runs:

```
gh run list
```

To open the build log in a browser:

```
gh run view --web
```

### 5.5 Confirm the Release page

Once both runs finish:

```
gh release view dag-v1.1.0
# or open in browser:
gh release view dag-v1.1.0 --web
```

You should see both `Cogniflow-UI-DAG-Windows.zip` and
`Cogniflow-UI-DAG-macOS.zip` listed under Assets.

The permanent student-facing URL is:

```
https://github.com/<you>/observability-masterclass/releases/latest
```

This URL always redirects to the most recent release — share it once,
update it never. (Note: `releases/latest` returns whichever release is
newest across all flavors. If you ship `cyclic-v0.1.0` after
`dag-v1.0.0`, students hitting `latest` will see the cyclic one. If
that's not what you want, share a flavor-pinned URL like
`releases/tag/dag-v1.1.0` instead.)

### 5.6 Edit release notes after the fact

```
gh release edit dag-v1.1.0 --notes "Manual release notes here."
```

Or edit on the website.

---

## 6. Repository settings worth setting up once

Open `https://github.com/<you>/observability-masterclass/settings`.

### 6.1 Workflow permissions

Settings → Actions → General → **Workflow permissions** → choose
**Read and write permissions**.

This is required so the `softprops/action-gh-release` step in the
workflows can attach files to Releases. If left at the default ("Read
repository contents permission"), the build succeeds but the Release
page stays empty.

### 6.2 Branch protection (optional but recommended for collaboration)

Settings → Branches → Add rule for `main`:

* Require pull request reviews before merging.
* Require status checks to pass before merging.

Not necessary for solo development — only relevant when others
contribute.

### 6.3 Default branch

Already `main` from the setup. If the GitHub UI shows `master` as
default, change it to `main`: Settings → Branches → switch default.

---

## 7. Secrets (for code-signing, future)

No secrets are required for the current pipeline. For future
macOS code-signing of the `.app` to bypass Gatekeeper, you'll add three
secrets on the repo:

Settings → Secrets and variables → Actions → New repository secret.

| Name | Where it comes from |
|---|---|
| `APPLE_ID` | Your Apple Developer account email |
| `APPLE_TEAM_ID` | Your team ID from https://developer.apple.com/account |
| `APPLE_APP_PASSWORD` | App-specific password from https://appleid.apple.com → Sign-In and Security |

Once those exist, extend `build-macos.yml` with `codesign` and
`xcrun notarytool submit` steps. The CI runners can read the secrets via
`${{ secrets.APPLE_ID }}` etc. — they are never printed in build logs.

Cost: $99/year for the Apple Developer Program. Until then, students
work around the Gatekeeper warning per `building.md` §6.

---

## 8. Common operations cheat sheet

```
# Status of your local checkout
git status

# What changed
git diff
git diff --staged

# Push current work
git add .
git commit -m "..."
git push

# Pull latest
git pull

# List branches
git branch -a

# Switch branches
git checkout main
git checkout -b feature/foo

# List recent CI runs
gh run list
gh run watch
gh run view --web

# Releases
gh release list
gh release view v1.0.0
gh release view v1.0.0 --web
gh release download v1.0.0

# Pull requests
gh pr list
gh pr create
gh pr view --web
gh pr merge

# Issues
gh issue list
gh issue create
gh issue close 42

# Repo
gh repo view --web
gh repo clone <user>/<repo>
```

---

## 9. Troubleshooting

### 9.1 `git push` asks for username and password

You're on HTTPS without `gh auth login` set up, or your credential
helper is misconfigured. Re-run:

```
gh auth login
gh auth setup-git
```

The `setup-git` step wires the credential helper so `git` itself
inherits the gh auth.

### 9.2 Push rejected — "non-fast-forward"

Someone (or your other machine) pushed first. Pull and replay:

```
git pull --rebase
git push
```

Resolve any conflicts that surface during the rebase, then continue.

### 9.3 Pushed a tag but no Release appeared

Three possible causes:

1. The workflow file (`build-windows.yml`/`build-macos.yml`) was added
   to the repo *after* the commit the tag points at. Tags only see the
   files in their own commit. **Fix:** delete the tag, re-commit, re-tag:

   ```
   git tag -d v1.0.0                  # delete locally
   git push origin :refs/tags/v1.0.0  # delete remotely
   git tag v1.0.0
   git push origin v1.0.0
   ```

2. Workflow permissions are wrong. See §6.1.

3. The workflow ran but `softprops/action-gh-release` failed silently.
   Check `gh run view --web` for the failed step.

### 9.4 Adding the cyclic UI workflows later

When the cyclic UI lands in `cyclic/cogniflow-ui/`, copy
`build-dag-windows.yml` and `build-dag-macos.yml` to
`build-cyclic-windows.yml` and `build-cyclic-macos.yml`, then in each:

1. Change `working-directory: dag/cogniflow-ui` →
   `working-directory: cyclic/cogniflow-ui`.
2. Change the tag trigger pattern from `dag-v*.*.*` →
   `cyclic-v*.*.*`.
3. Change all `Cogniflow-UI-DAG-...` zip names →
   `Cogniflow-UI-Cyclic-...`.
4. Change all artifact upload paths from
   `dag/cogniflow-ui/dist/...` → `cyclic/cogniflow-ui/dist/...`.
5. Change the `if: startsWith(github.ref, 'refs/tags/dag-v')` guard
   to `'refs/tags/cyclic-v'`.

The two flavors then build independently, gated by their own tag
prefixes.

### 9.5 Accidentally committed a secret

Even on a private repo, treat the secret as compromised: it's now in git
history. Rotate it (revoke + regenerate at the source), then run:

```
git filter-repo --invert-paths --path <leaked-file>
git push --force
```

Force-pushing rewrites history; coordinate with anyone else with a
checkout. Better practice: never add secret files; if you must, encrypt
them or use environment variables / GitHub Secrets.

### 9.6 Free CI minutes running low (private repo only)

Settings → Billing and plans → Plans and usage. The 2000 free
minutes/month reset on your billing day. Each release uses ~10–15
minutes total across both runners, so you can ship 100+ releases per
month before exhausting the quota.

If you run out, either:

* Convert the repo to public (unlimited free minutes).
* Pay for additional minutes ($0.008/min for Linux, $0.08/min for
  macOS).

### 9.7 Want to delete the wrong release

```
gh release delete v1.0.0
git push origin :refs/tags/v1.0.0   # also delete the tag
```

Then fix and re-tag.

---

## 10. Long-term repo hygiene

* **Tag every shipped version.** Don't ship a build that isn't tied to a
  tag — students need to be able to point at a specific version when
  reporting bugs.
* **Keep the README on the main page student-friendly.** The first thing
  anyone sees should be: what is this, how do I get the binary, how do I
  contact you for help. Detailed dev docs live in side files.
* **Use Releases as the changelog.** Each release page can have notes —
  what changed, what's new in the bundled pipelines. Avoid keeping a
  separate `CHANGELOG.md` in sync; the Releases page is your source of
  truth.
* **Pin issues for FAQ.** When a student question recurs, file an issue
  with the answer and pin it — newcomers see it first when browsing
  Issues.
