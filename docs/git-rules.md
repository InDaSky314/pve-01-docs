# Git Operating Rules for pve-01

**Scope:** Mandatory operating rules for all agents (Claude Code, agy) and human operators working in `/root/pve-01-docs` and associated repositories.  
**Standing Principle:** Git is the single source of truth. The working tree, running services, and container filesystems are derived state.

---

## 0. Current State (verified 2026-09-05, update this section when it changes)

| Fact | Value |
|---|---|
| Primary | `https://github.com/InDaSky314/pve-01-docs.git` — **public** (`gh api` confirms `visibility=public`) |
| Mirror | `git@github-mirror:nk-sys-ops/pve-01-docs.git` — **visibility not verifiable from this host**; `gh api repos/nk-sys-ops/pve-01-docs` returns 404 to our token. Treat it as public: never place anything there you would not publish. |
| Post-replay baseline anchor | `8edfd3b` (356 commits) — the point both remotes were reconciled to on 2026-09-05. All later work fast-forwards from here. |
| Local `main` | tracks `origin/main`; realign with the preflight, never assume |
| Active work branch | `epg-find-and-mct-verification` |

**This table does not track the current tip, deliberately.** Recording a live
SHA here is self-invalidating: updating the table is itself a commit, so the
number it states is wrong the moment it lands. The baseline anchor above is
fixed and stays correct. For live values run the §0a preflight — that is the
only trustworthy source.

**How this state was reached.** On 2026-09-04 the primary was force-pushed
backwards to an identity-scrubbed history, orphaning 59 commits of local work
(see `docs/incident-force-push-20260904.md`). On 2026-09-05 those commits were
**replayed**, not force-pushed: the primary tip and the local base commit had
identical trees (`e54a66ff…`), so each commit was rebuilt with `git commit-tree`
reusing the original tree object, re-parented onto the primary tip, and given
sanitised authorship. Tree identity was asserted at every step and the final
tree compared byte-for-byte against the preserved local work before anything
was pushed. The result was a **fast-forward**. The mirror, which sat on the old
lineage, was then realigned by force with the owner's explicit approval.

One line was redacted during the replay: `docs/dvr-reporting-20260902.md`
carried the owner's residential Telekom WAN IP. It had not yet reached either
remote, so redacting it inside the replay kept it out of published history
entirely. **Public IPs belonging to the household are not publishable. Shared
VPN exit IPs are.**

---

## 0a. Preflight — run before any commit or push

**Before committing** — identity must be right *before* the object is written;
there is no fixing it afterwards without a rewrite:

```bash
cd /root/pve-01-docs
git config user.name     # MUST print: root
git config user.email    # MUST print: 169815609+InDaSky314@users.noreply.github.com
```

**Before pushing:**

```bash
cd /root/pve-01-docs
git fetch origin
git update-ref refs/heads/main "$(git rev-parse origin/main)"   # keep local main honest
git rev-parse --short HEAD main origin/main
git rev-list --left-right --count main...origin/main
git log --format='%an <%ae>' origin/main..HEAD | sort -u   # ONLY the noreply address
git log -p origin/main..HEAD | grep -nE '^\+.*[0-9]{1,3}(\.[0-9]{1,3}){3}'  # review any IP you are about to publish
```

If `git config user.email` prints nothing, **stop and set it** (§5.1). An unset
value silently falls back to `root@pve-01.jetta.tech` — the exact identity this
repository exists to keep out of public history.

## 1. Golden Rules of Branch & Remote Discipline

1. **Never force-push a shared branch (`main`).**
   - Direct force-pushes (`git push --force`, `git push -f`, or `git push +ref`) to `main` are strictly prohibited.
   - Branch protection on GitHub (`InDaSky314/pve-01-docs`) must remain enabled with `allow_force_pushes=false`.
   - Any historical rewrite (rebase, filter-repo, squash) must happen on an isolated feature branch and be reviewed before merging.

2. **Understand the asymmetric dual-remote setup.**
   - `origin` in `/root/pve-01-docs/.git/config` has two push URLs:
     - Primary: `https://github.com/InDaSky314/pve-01-docs.git` (HTTPS via `gh`, public)
     - Mirror: `git@github-mirror:nk-sys-ops/pve-01-docs.git` (SSH deploy key, private backup)
   - **`gh pr merge` updates ONLY the primary server-side.** The mirror does NOT receive PR merges automatically.
   - After merging a PR on GitHub, you MUST synchronize the mirror:
     ```bash
     git -C /root/pve-01-docs fetch origin
     git -C /root/pve-01-docs update-ref refs/heads/main "$(git -C /root/pve-01-docs rev-parse origin/main)"
     git -C /root/pve-01-docs push origin "$(git -C /root/pve-01-docs rev-parse origin/main)":main
     ```
     The source is an explicit SHA, never a bare branch name — see §6.1.
   - Be aware: external clones (e.g. laptop/MacBook) often only have the primary remote configured. Pushes from external machines do not update the mirror.

3. **Verify what a push actually did on BOTH remotes.**
   - Never trust a bare exit code `0` or an echo statement like `pushed <sha>`.
   - When pushing to multiple remotes, Git evaluates URLs sequentially. If the first fails or diverges, behavior depends on flags. Always verify the actual ref on both remotes after critical operations:
     ```bash
     git ls-remote origin refs/heads/main
     git ls-remote git@github-mirror:nk-sys-ops/pve-01-docs.git refs/heads/main
     ```

---

## 2. Early Detection of Divergence

Before modifying local files or running automated sprints:

1. **Always fetch before inspecting or building:**
   ```bash
   git fetch origin
   ```
2. **Watch for the warning flag in fetch output:**
   - Any output containing `(forced update)` means the remote branch was rewritten or moved backwards.
     ```
     + 5015ad1...baf4bad main -> origin/main (forced update)
     ```
3. **Check divergence status explicitly:**
   ```bash
   git rev-list --left-right --count main...origin/main
   ```
   - Expected during clean state: `0  0` (or `N  0` if you have unpushed local commits).
   - If the right-hand number is non-zero, the remote has commits you do not have.
   - If `git merge-base main origin/main` fails with exit code 1, the histories share NO common ancestor (history was rewritten).

---

## 3. Incident Protocol: What To Do When Divergence Is Found

When remote divergence or a forced update is detected:

1. **STOP IMMEDIATELY.**
   - Do NOT run `git pull`, `git merge`, or `git rebase`.
   - Do NOT run `git reset --hard origin/main` (this destroys local work).
   - Do NOT run `git push --force` to "fix" the remote (this can overwrite remote security fixes or privacy scrubs).
2. **Preserve local work with an annotated tag:**
   ```bash
   git tag -a "local-work-$(date +%Y%m%d%H%M%S)" -m "Preserving local commits prior to divergence investigation"
   ```
3. **Inspect the mirror first:**
   - Check if the mirror has the un-diverged history:
     ```bash
     git ls-remote git@github-mirror:nk-sys-ops/pve-01-docs.git refs/heads/main
     ```
4. **Inspect GitHub repository activity:**
   - Find who pushed, when, and what changed:
     ```bash
     gh api repos/InDaSky314/pve-01-docs/activity | jq '.[0]'
     ```
5. **Report findings to the owner:**
   - State the diverging SHAs, commit counts, and whether trees or commit metadata were modified. Do not execute destructive recovery without owner sign-off.

---

## 4. The Mirror Is a Recovery Aid, Not an Oracle

- The secondary mirror is an emergency backup and audit witness.
- It is **not** an oracle: because `gh pr merge` does not mirror PRs automatically, the mirror legitimately lags the primary during standard PR-driven workflows.
- Conversely, if the primary is force-pushed from an external machine lacking the mirror remote, the mirror will preserve the pre-incident history.
- Never blindly overwrite one remote with the other without comparing tree hashes (`git diff-tree`) and commit counts first.

---

## 5. Identity & Privacy Rules on Public Repositories

1. **Use the privacy noreply email for all commits in this repository:**
   ```bash
   git config user.name "root"
   git config user.email "169815609+InDaSky314@users.noreply.github.com"
   ```
   The name is `root` because all 356 commits in the published history use it;
   GitHub attributes by email, so the noreply address is the part that matters.
   **These are repo-local settings and they were found UNSET on 2026-09-05** —
   every commit made in that state carried `root@pve-01.jetta.tech`. Verify with
   `git config user.email`; do not assume the rule is in force because it is
   written here.
2. **Never commit personal emails (`@outlook.com`) or internal domain names (`*.jetta.tech`).**
3. **Never run history-rewriting tools (`git filter-repo`, `git filter-branch`) on a stale checkout:**
   - Always run `git pull` immediately before any history scrubbing operation to ensure you are rewriting the current tip, not clobbering recent days of work.

---

## 6. Rules earned on 2026-09-05

### 6.1 Never push a bare branch name as the source ref

A push of `main:main` was issued while local `main` was a **stale branch on a
dead lineage** (`03dab7f`, pre-scrub). The push succeeded, did exactly what it
was told, and put the mirror on a history that shared no ancestor with the
primary. `--force-with-lease` did not help: the lease guards the *destination*,
not the *source*.

- Push an explicit, verified SHA: `git push <remote> <sha>:main`. No exceptions,
  and no "I checked it first" — the check and the push must be the same value,
  which only an explicit SHA guarantees.
- Keep local `main` honest: `git branch -f main origin/main` after any replay
  or remote rewrite. A stale `main` is a loaded gun.

### 6.2 The mirror may be force-realigned only under all four conditions

Force-pushing the mirror is sanctioned **only** when every one of these holds:

1. The owner has explicitly approved that specific realignment.
2. `--force-with-lease=main:<current-remote-sha>` pins the destination.
3. The content being discarded is proven to be a subset — show it:
   `git diff --stat <mirror-tip> <new-tip>` must contain nothing you cannot
   account for.
4. The source ref is an explicit SHA (§6.1).
5. **The destination is the mirror's own URL, never `origin`.** `origin` carries
   two push URLs, so `git push --force origin …` aims the force at the primary
   as well. Push the mirror directly:
   ```bash
   git push --force-with-lease=main:<current-mirror-sha> \
       github-mirror:nk-sys-ops/pve-01-docs.git <sha>:main
   ```

The primary is never force-pushed. If the primary needs history changed, replay
onto its tip as in §0 and let it fast-forward.

### 6.3 A rule written in this file is not a rule in force

Three defects in one day shared a shape: a correct guard existed but was never
reached. `check_single_tuner_conflict()` was MCT-aware but unreachable from the
proactive scheduler; the PPV fallback's idempotency check keyed on a field
Jellyfin rewrites; §5.1 mandated an identity that was never configured.

Before relying on any control — in code or in this document — verify it is
actually in effect. Grep the callers. Print the config value. A documented
workaround is a marker for an unfixed root cause, not a fix.

### 6.4 Both agents share this file

Claude Code and `agy` both operate in this repository and neither can see the
other's session. This document is the handoff. When either agent changes the
remote state, branch layout, or identity configuration, it updates §0 in the
same commit — otherwise the other agent is working from a stale map, which is
how `main:main` happened.
