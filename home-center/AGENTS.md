# Home Center agent contract

This file is the repository-level operating contract for coding agents and automated development workflows.

## Product boundary

- Home Center is an independent product. Do not mix it with Control Center or any development-infrastructure product.
- Product runtime, build output, deployment artifacts, public APIs and product documentation must remain infrastructure-neutral unless a product requirement explicitly says otherwise.
- Never add development orchestration, agent, chat, model-provider or internal CI infrastructure as a Home Center product dependency.

## Source of truth

- GitHub repository state is authoritative for implementation status: branches, commits, Issues, Pull Requests and GitHub Actions evidence.
- `.hc-dev/releases.json` is the machine-readable release/workstream registry.
- Chat history, agent memory and prose status reports are not release authority.
- Do not claim a workstream complete unless its configured repository evidence is satisfied.

## Release isolation

- Every implementation change must target one explicit release or `main` maintenance scope.
- Release work must use the matching `release/X.Y.Z` base branch unless an issue explicitly records another integration path.
- Never silently copy changes between parallel release branches.
- Cross-release reuse must be an explicit merge/cherry-pick/port with independent CI on the target branch.

## Required task shape

Before coding, identify:

1. target release;
2. tracking Issue;
3. one bounded workstream/vertical slice;
4. acceptance criteria;
5. safety/failure/recovery impact;
6. required tests and documentation impact.

A Pull Request must state the target release and tracking Issue and must preserve the repository PR checklist.

## Engineering rules

- Prefer closed, typed, versioned contracts and deterministic behavior.
- Preserve default-deny/fail-closed security boundaries.
- Do not introduce generic shell/root command APIs, caller-controlled privileged paths, or secret-bearing logs/evidence.
- Preserve explicit production-activation boundaries. Code merge/release publication is not production deployment authority.
- Do not mutate Samba AD, DNS, DHCP, Domain SID, replication topology, GPO or other protected infrastructure implicitly.
- Add or update tests for every behavioral change and for relevant negative/security cases.
- Run `make ci` before declaring an implementation slice complete whenever the branch supports it.

## Product documentation boundary

Product-facing documentation may describe only Home Center architecture, capabilities, operation, security model, deployment requirements and user/operator behavior.

Do not disclose or describe internal development sources or methodology, ChatGPT/Codex usage, AI Development Infrastructure/Fabric/Orchestrator, CI Fabric, internal agent topology, reviewer-agent design, model providers, prompts or chat workflows in product-facing documentation.

Development-control material belongs only under `.hc-dev/`, `AGENTS.md`, GitHub workflow metadata, or other explicitly engineering-only locations.

## Completion rule

A task is complete only when:

- required code/contracts/docs are committed;
- local deterministic gates required by the branch pass;
- GitHub-hosted required checks pass on the exact PR head;
- acceptance criteria are backed by repository evidence;
- unresolved review/security findings are closed;
- release registry evidence remains accurate.

Final development reports should render release/workstream progress from `.hc-dev/releases.json` and live GitHub evidence, not from estimates.