# October 2026 compatibility and scheduling update

## Version evidence

Local host: ChatGPT **26.928.31416 (12553)** on Apple Silicon; bundled Codex CLI **0.159.2**. The new CLI is at `Contents/Resources/codex-cli/bin/codex`; older `Contents/Resources/codex` installations remain supported. App Server remains experimental. Matching a version does not prove the private desktop bridge or UI works.

`GET /api/diagnostics` reports host, protocol, and UI adapters separately. The UI probe checks message transport and sidebar bootstrap without starting tasks. Private worktree routes remain explicitly unverified in the general probe. The checked-in request schemas in `tests/fixtures/codex-0.159.2` were generated from the installed binary with `app-server generate-json-schema --experimental` on 2026-10-01.

The new sidebar keeps its navigation outside `data-app-action-sidebar-scroll`. The injector now finds the visible fixed navigation row, retains the old layout fallback, removes cloned native routing attributes, and handles the new image-based icon. This fixes the missing Taskboard entry. Only visible content viewports are selected.

## Planning and launch are independent

Agent-assisted planning defaults **off**, including after migration. Manual dependencies, groups, and parallel work remain available. Auto-claim is a separate setting. Switching planning off does not interrupt running work or rewrite accepted dependencies; pause remains explicit.

Enabled planning starts a read-only, non-escalating thread, inspects repository instructions and code, and returns an editable proposal with dependencies, write scopes, parallelism rationale and acceptance criteria. The operation binds the workspace, committed baseline, group version, and child versions. Changed or dirty repositories and changed task graphs require regeneration. Confirmation creates tasks atomically and never launches them. The existing deterministic scheduler owns execution; the planner cannot dispatch native subagents around it.

## Verification and shared resources

Managed merges now require explicit project verification commands. Set them under **Automation → Concurrency and verification** as JSON argv arrays, for example `[["npm", "test"], ["python", "-m", "unittest", "discover", "-s", "tests"]]`. Commands run without an implicit shell in the isolated merge candidate, including group final integration. Empty configuration blocks publication instead of claiming untested success. Ordinary legacy tasks retain their existing lifecycle.

Evidence records the exact candidate, source and target SHA, command arguments, exit code, bounded log tail and timing. Each command has a five-minute timeout. Failure, dirty output, a changed candidate, moved target or changed policy prevents publication. Tests should write generated files only to ignored paths. Recovery cannot mark an already-published merge complete without matching passing evidence. Unknown verification-process state blocks retry and retains reservations for inspection; it is never cleared just because time elapsed. A deliberately new candidate can be verified after the old process has been checked.

A repository concurrency budget of zero preserves the previous unlimited behavior. Aliases of the same Git repository use the strictest nonzero budget. Unknown execution leases count against it. Named resources such as `port:3000`, `browser:main`, and `db:test` are exclusive across the board, persist through restart, and stay reserved through integration or a confirmed pause. These declarations coordinate board-managed work; they cannot intercept external processes or undeclared resource use.

Quota continuations are released gradually through normal scheduling. Native terminal/status events invalidate history caches; compensation reads run at 60 seconds for active tasks and five minutes for inactive history, with immediate refresh on observed reconnect and history loading on demand. These are fallback bounds, not a promise to detect missed events instantly.

## Validation and limits

Local tests cover protocol request shapes, Plan questions/approvals, steering, pause/recovery, quotas, SQLite migration, resource leases, concurrent claims, graph confirmation and actual Git integration. Verification tests execute real subprocesses and confirm failures cannot advance the target branch. CI runs the contracts and frontend build on macOS and Windows; the existing Windows packaging job remains enabled. Neither workflow publishes releases.

Native Mac smoke used an isolated desktop profile and an isolated Git repository. It verified App Server initialization, thread creation, renderer message transport, model listing, managed worktree creation/owner association, sidebar injection, a ready embedded board, entry restoration after DOM removal, and entry/frame recovery after a renderer reload. Full model-driven Plan question/approval, quota exhaustion and client restart scenarios remain contract-tested rather than claimed as live acceptance. Windows desktop interaction still requires a Windows acceptance run.
