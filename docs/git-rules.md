# Git Operating Rules for pve-01

**Scope:** Mandatory operating rules for all agents (Claude Code, agy) and human operators working in `/root/pve-01-docs` and associated repositories.  
**Standing Principle:** Git is the single source of truth. The working tree, running services, and container filesystems are derived state.

---

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
     git -C /root/pve-01-docs checkout main && git pull && git push origin main
     ```
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
   git config user.name "InDaSky314"
   git config user.email "169815609+InDaSky314@users.noreply.github.com"
   ```
2. **Never commit personal emails (`@outlook.com`) or internal domain names (`*.jetta.tech`).**
3. **Never run history-rewriting tools (`git filter-repo`, `git filter-branch`) on a stale checkout:**
   - Always run `git pull` immediately before any history scrubbing operation to ensure you are rewriting the current tip, not clobbering recent days of work.
