# Publishing PIPEBREAK to GitHub

**Nothing in this document has been executed.** It is a checklist for when you
decide to publish. Work through it in order.

---

## Before anything else: the secret

`.env` contains a live Groq API key. If it reaches GitHub — even in a commit
that is later deleted — treat the key as compromised and rotate it, because
GitHub retains unreferenced objects and bots scrape public repos within minutes.

The repository is already set up to prevent this:

- `.gitignore` excludes `.env` on its first content line.
- `.env.example` holds only a placeholder and is safe to commit.
- `config.load_env()` deliberately ignores any value starting with `your_`, so a
  fresh clone fails with a clear message rather than silently sending a
  placeholder to the API.

**Verify before the first commit:**

```bash
cd ~/Anto/PIPEBREAK
git status --short          # .env must NOT appear
git check-ignore -v .env    # must print the .gitignore rule that catches it
```

If `git check-ignore` prints nothing, stop. The file is not ignored.

---

## What gets published and what does not

| Included | Why |
|---|---|
| `pipebreak/`, `dbt/`, `server/`, `ui/src/` | The actual work |
| `README.md`, `docs/` | So a reader understands it in five minutes |
| `requirements.txt`, `ui/package.json` | So it is reproducible |
| `sweep.sh` | Resumes a sweep across days of free-tier quota |
| `LICENSE` | MIT, as the README states |
| `.env.example` | The shareable template |
| `results/scores_*.json`, `results/report.md` | The evidence, ~16 KB |

| Excluded | Why |
|---|---|
| `.env` | Live secret |
| `work/` (536 MB) | Rebuilt deterministically by `python -m pipebreak build` |
| `.venv/`, `ui/node_modules/` (67 MB) | Reinstalled from the manifests |
| `results/progress_*.json`, `results/sweep_*.log` | Transient noise |

A reviewer reproduces everything excluded with three commands. That is the point
of the fixed seed.

One judgement call: `work/runs/*/` holds the full agent transcripts, and those
are genuinely interesting evidence. They are inside the ignored `work/`
directory. If you want them public, copy the ones you care about into
`results/transcripts/` before committing — they are small JSON files.

---

## Step 1 — Initialise

```bash
cd ~/Anto/PIPEBREAK
git init
git branch -M main
```

`git init` creates the local repository. `git branch -M main` renames the default
branch, because GitHub expects `main` and older Git versions still create
`master`.

---

## Step 2 — Confirm what is about to be committed

This is the step people skip and regret.

```bash
git add -A
git status --short
```

Read the list. It should be roughly 40–60 files. If you see `.env`, `.venv/`,
`work/`, or `node_modules/`, unstage everything and fix `.gitignore` first:

```bash
git reset          # unstages, changes no files on disk
```

A useful extra check — search the staged content for anything resembling a key:

```bash
git diff --cached | grep -n "gsk_" || echo "clean: no Groq key in staged content"
```

---

## Step 3 — First commit

```bash
git -c user.name="Anthony Breeganzo Thomas" \
    -c user.email="anthonybreeganzo02@gmail.com" \
    commit -m "PIPEBREAK: a benchmark for agent judgment in data pipeline repair"
```

Using `-c` sets the identity for this one commit rather than globally, which
avoids changing the Git configuration on your machine. If you would rather set
it once for this repository:

```bash
git config user.name  "Anthony Breeganzo Thomas"
git config user.email "anthonybreeganzo02@gmail.com"
```

---

## Step 4 — Create the empty remote repository

On <https://github.com/new>:

- **Name:** `pipebreak`
- **Description:** *A benchmark measuring whether AI agents know when not to fix a data pipeline.*
- **Visibility:** Public — the whole point is that someone can read it
- **Do not** tick "Add a README", "Add .gitignore" or "Choose a licence"

That last point matters. Initialising the remote with files creates a commit
your local repository does not have, and the first push is then rejected as
non-fast-forward. Starting empty avoids the problem entirely.

---

## Step 5 — Connect and push

```bash
git remote add origin https://github.com/<your-username>/pipebreak.git
git push -u origin main
```

`-u` records the link between local `main` and remote `main`, so later pushes
are just `git push`.

When prompted for a password, GitHub will **not** accept your account password.
Use a Personal Access Token: *Settings → Developer settings → Personal access
tokens → Tokens (classic) → Generate new token*, with the `repo` scope. Paste
the token as the password.

To avoid re-entering it:

```bash
git config --global credential.helper osxkeychain
```

---

## Step 6 — Check the result in the browser

- The README renders, and the Mermaid diagrams in `docs/HLD.md` display as
  diagrams rather than code blocks. GitHub renders Mermaid natively; if a
  diagram shows as text, the fence language is wrong — it must be ` ```mermaid `.
- `.env` is **not** in the file list.
- The repository is a few hundred kilobytes, not hundreds of megabytes. If it is
  large, something under `work/` slipped through.

---

## Step 7 — Make the front page do its job

Add repository topics: `benchmark`, `llm-evaluation`, `ai-safety`,
`data-engineering`, `duckdb`, `agents`.

Set the "About" description to the same one-liner as above. Most people decide
whether to read a repository from that sentence and the first screen of the
README.

Consider adding a screenshot of the dashboard near the top of the README — a
working UI is far more persuasive than a description of one:

```markdown
![PIPEBREAK dashboard](docs/dashboard.png)
```

---

## Later commits

```bash
git add -A
git status --short      # always look before committing
git commit -m "Add cost-adjusted scoring"
git push
```

---

## If the key ever does leak

Do these in order, and do the first one first:

1. **Revoke the key** at <https://console.groq.com/keys>. Do this before
   anything else. Removing a file from history does not help if the key is
   already scraped.
2. Issue a new key and put it in your local `.env`.
3. Only then worry about rewriting history — with `git filter-repo`, or by
   deleting the repository and starting a fresh one if it is young.

Removing the file and committing the removal is **not** sufficient. The old blob
stays reachable in the repository's history.

---

## Suggested repository description

> A benchmark measuring whether AI agents know when **not** to fix a data
> pipeline. 16 planted incidents in a synthetic warehouse — 12 repairable, 4
> impossible to repair honestly. Agents are scored on detection, repair fidelity,
> blast radius, and judgment. An agent that "fixes" all 16 scores worse than one
> that fixes 12 and escalates 4.
