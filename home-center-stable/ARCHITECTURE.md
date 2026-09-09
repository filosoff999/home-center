# Architecture

Home Center has a Python control plane, a static Web interface, a local SQLite
state store, and closed JSON contracts. The runtime exposes separate Web and
mutually authenticated peer listeners. Planning components return typed,
non-executing plans. A root service exposes only fixed helper actions over a
local Unix socket; callers cannot supply executable paths or shell commands.

Peer reconciliation is collection-based and deployment profiles support one to
64 nodes. The supplied two-node topology remains single-writer. Health,
infrastructure inventory, backup, audit, module admission, resource snapshots,
and release identity remain explicit subsystems with fail-closed input
validation.
