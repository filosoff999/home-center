# Home Center Development Control

`.hc-dev/` is engineering-only control metadata. It is not Home Center product documentation and must not be included in runtime or release artifacts.

## Authority

`releases.json` defines the tracked release lines, workstreams, weights and repository evidence that determine development progress. A chat message, estimate or agent memory never overrides this registry or live GitHub state.

Supported evidence kinds:

- `pr`: complete only when the referenced Pull Request satisfies the configured state (normally `merged`);
- `issue`: complete only when the referenced Issue satisfies the configured state (normally `closed`).

A workstream with no evidence is intentionally `0%`: branch existence, local work or an unmerged/untracked chat claim does not count as completed repository evidence.

## Agent workflow

1. Read `AGENTS.md` and `.hc-dev/releases.json`.
2. Select exactly one target release and workstream.
3. Create/use a tracking Issue with explicit acceptance criteria.
4. Implement on a feature branch targeting the matching release branch.
5. Run local required gates.
6. Open a PR using the repository template.
7. Wait for exact-head GitHub checks and resolve review findings.
8. Merge only when gates are green.
9. Add/update repository evidence in `releases.json` when a new planned workstream or completion signal is introduced.

## Status

Run locally:

```bash
python tools/hc_dev_control.py validate
python tools/hc_dev_control.py status
```

With `GITHUB_TOKEN` and network access, `status --github` resolves live evidence and renders the canonical progress bars. GitHub Actions runs the same logic automatically.

## Product-documentation isolation

Internal orchestration details stay here. Product-facing documentation must not describe agent/chat/model-provider topology or internal development infrastructure.