# Contributing to MurOS

Thanks for taking the time to look at MurOS. This document explains how
to report issues, propose changes and get them merged.

## Project philosophy

Before opening a feature request, please read the "no plugins, all
native" section of the README. MurOS deliberately stays narrow in
scope: every feature ships in the core, is tested end-to-end, exposed
in the UI and documented in the same place. We say no to a lot of
otherwise reasonable ideas, not because they are bad, but because they
would dilute the product.

If you are not sure whether an idea fits, open a discussion first
rather than a pull request.

## Reporting bugs

Open an issue at <https://github.com/murosorg/muros/issues> with:

- MurOS version (`dpkg -l muros` or the release tag you installed from)
- Debian version (`cat /etc/debian_version`)
- What you did, what you expected, what happened
- Relevant logs: `journalctl -u muros-backend -n 200` and, for kernel
  side issues, `journalctl -k -n 200`
- If the bug is in the UI: browser, console errors, a screenshot

Do not open a public issue for security problems. See `SECURITY.md`.

## Proposing changes

1. Open an issue describing the problem before you start coding. This
   avoids wasted work on something that will be rejected on scope.
2. Fork the repo, create a feature branch from `main`.
3. Keep the change focused. One PR, one topic. Unrelated cleanups go in
   separate PRs.
4. Update tests, docs and the changelog entry for the next release.
5. Open a pull request against `main`.

## Development setup

```
make install   # Python venv + npm packages
make backend   # FastAPI on :8000
make frontend  # Vite dev server on :5173
```

Backend tests:

```
cd backend
. .venv/bin/activate
pytest
```

Frontend type-check and lint:

```
cd frontend
npm run typecheck
npm run lint
```

The CI runs the same commands on every push. A red CI blocks the merge.

## Code style

- **Language**: the entire repository is in English, including code
  comments, commit messages, variable names and UI strings. French is
  fine in private discussions, not in the codebase.
- **Python**: formatted with `ruff format`, linted with `ruff check`.
  Type hints on public functions. SQLAlchemy 2 style, no legacy Query
  API.
- **TypeScript**: strict mode on. No `any` unless justified in a
  comment. React function components, hooks only, no class components.
- **CSS**: Tailwind utilities. No inline styles unless dynamic. Stick
  to the existing palette and spacing scale.
- **UI tone**: sober, technical, no emoji, no exclamation marks, no
  marketing language. Buttons say what they do, not "Let's go".
- **No em dashes** in user-facing text or comments.

## Commit messages

Short imperative subject line, optional body for context. Reference
the issue when applicable.

```
fix(wireguard): keep peer order on apply

The previous code sorted peers by name before writing wg0.conf, which
made the running config diff against the DB on every apply. Now the
order from the DB is preserved.

Refs #142
```

Conventional commit prefixes (`feat`, `fix`, `docs`, `chore`, `refactor`,
`test`) are used but not strictly enforced.

## Pull request review

A PR needs:

- Green CI
- One maintainer approval
- A changelog entry for the next release if the change is visible to
  users
- Updated docs and screenshots if the UI changed

Be patient with reviews. MurOS has a small team. We try to answer
within a week.

## License

By contributing you agree that your contribution is licensed under the
GNU AGPL v3.0 or later, the same license as the rest of the project.
You confirm that you have the right to submit the code, either because
you wrote it yourself or because the license of the original code is
compatible with AGPL-3.0+.
