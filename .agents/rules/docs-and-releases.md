# Docs and release rules

Prefix: DOC.

## Docs move with the code

- **DOC1:** A change that alters behaviour a user or agent can see updates the docs in the same change. The owners:
  - routes, auth, health, terminal, account, broker time: [docs/rest-api.md](../../docs/rest-api.md) (it also indexes every endpoint);
  - symbols, ticks, rates, TA: [docs/market-data.md](../../docs/market-data.md);
  - orders, positions, history: [docs/trading-and-history.md](../../docs/trading-and-history.md);
  - backtests: [docs/backtesting.md](../../docs/backtesting.md), [docs/backtest-optimization.md](../../docs/backtest-optimization.md);
  - `/compile`: [docs/compiling.md](../../docs/compiling.md); file API: [docs/files.md](../../docs/files.md);
  - Chart Deployments: [docs/chart-deployments.md](../../docs/chart-deployments.md), and the loader protocol in [docs/chart-control-protocol.md](../../docs/chart-control-protocol.md);
  - MCP tools and agent integrations: [docs/mcp-and-agents.md](../../docs/mcp-and-agents.md) plus the agent skill in `.agents/skills/mt5-httpapi/` and the plugin README in `.agents/plugins/mt5-httpapi/`;
  - config settings: `config/config.yaml.example` and [docs/installation-and-configuration.md](../../docs/installation-and-configuration.md);
  - Make targets, tests, ports, recovery, logs: [docs/operations.md](../../docs/operations.md) and `make help` in the `Makefile`;
  - Go client: [docs/clients-and-examples.md](../../docs/clients-and-examples.md); multi-VM: [docs/multi-vm-setup.md](../../docs/multi-vm-setup.md).
- **DOC2:** `README.md` stays a short entry point that links to `docs/`. Add detail to the owning doc, not to the README; update the README's doc table when a doc file is added.
- **DOC3:** A changed project convention updates [AGENTS.md](../../AGENTS.md) or the matching file in `.agents/rules/` in the same change.
- **DOC18:** `docs/` is the GitHub Pages site (Just the Docs, pinned in `docs/_config.yml`). Every page starts with front matter: `title` (the navigation label, unique), a hand-written `description` of 50 to 170 characters naming the page's real subject, `nav_order`, and `parent` for a page that sits under another (whose front matter has `has_children: true`). Keep one `#` heading per page and the directory flat, because a file name is its URL.
- **DOC19:** Link between pages with the relative `.md` path. Link a file outside `docs/` with its full `https://github.com/psyb0t/mt5-httpapi/blob/master/<path>` URL (`tree/` for a directory), because a `../` link is broken on the site. The README links to pages by their full site URL, so its links still work where the README is mirrored.
- **DOC20:** After changing `docs/`, run `make docs` and open the output; a code block holding `{{` or `{%` needs the `{% raw %}` wrapper or Jekyll eats it.

## Prose style

- **DOC4:** No em dashes or en dashes in new or changed prose: docs, README, CHANGELOG, rules, docstrings, comments, commit messages, tag messages. Use a comma, colon, parentheses or a new sentence. Nothing enforces this yet and older docs still contain dashes; remove them from lines you edit.
- **DOC5:** Plain, specific words. State what happens and why, with real route names, field names and numbers. No filler or marketing words ("leverage", "robust", "seamless", "delve", "powerful", "simply").
- **DOC6:** Headings are sentence case (`## What the API refuses`, not `## What The API Refuses`), as in `docs/files.md` and `docs/compiling.md`.
- **DOC7:** In new Markdown, keep each prose paragraph on one source line, as `docs/files.md` and `CHANGELOG.md` do. Match the existing wrapping when editing an older hard-wrapped file.
- **DOC8:** The README and several older docs use a crude, joking voice; `docs/files.md`, `docs/chart-deployments.md` and `docs/compiling.md` use a plain one. Match the voice of the file you edit, and use the plain voice in new files.
- **DOC9:** Anything written to other people (release notes, issue and PR replies) speaks as one person: "I", never "we", "our" or "the team".

## CHANGELOG

- **DOC10:** Every user-visible change gets a `CHANGELOG.md` entry. A new release goes at the top, below the `---` under the intro, as `## [vX.Y.Z]: YYYY-MM-DD` followed by `### Added`, `### Changed`, `### Deprecated`, `### Removed`, `### Fixed` or `### Security` as needed.
- **DOC11:** Each bullet is written for the user: what they will see, then what changed. Lead notable items with a bold one-sentence summary. A `Fixed` bullet says what went wrong and under which conditions before saying what happens now. Link the owning doc (`See [docs/files.md](docs/files.md).`). Name renamed or removed variables, fields and routes explicitly with their replacements.
- **DOC12:** Version numbers follow Semantic Versioning as the CHANGELOG intro states: patch for fixes and docs, minor for compatible features and deprecations, major for breaking changes such as removing the deprecated camelCase keys (API5).

## Versions, tags and publishing

- **DOC13:** A release sets the same version in `.agents/.codex-plugin/plugin.json`, `.agents/plugins/mt5-httpapi/package.json` and `.agents/plugins/mt5-httpapi/openclaw.plugin.json`. `.agents/.claude-plugin/plugin.json` has no version field.
- **DOC14:** Each release is an annotated tag `vX.Y.Z` on the release commit. The tag message is the version on the first line, a blank line, then that release's CHANGELOG bullets (`git show v4.23.0` for an example).
- **DOC15:** Pushing a `v*` tag runs `.github/workflows/pipeline.yml`; after `test` and `lint` pass, the `clawhub` job publishes the skill (its version mirrors the tag) and the OpenClaw plugin (its version comes from `package.json`) to ClawHub. Bump the versions (DOC13) before tagging, or the plugin publishes under the old number.
- **DOC16:** Commit subjects follow `type(scope): summary` as in the history (`feat(files): ...`, `fix(nginx): ...`, `test(live): ...`), with a body that explains what changed and why.
- **DOC17:** Never commit, tag, push, publish or touch issues unless the user asked for that specific action in the current turn. Never put `Closes #N`, `Fixes #N` or `Resolves #N` in a commit, PR or tag unless the user said to close that issue.
