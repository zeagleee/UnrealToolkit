## What and why

<!-- What this changes, and the TODO.md task it does (N1, N2...) if any. -->

## How it was checked

- [ ] `python -m unittest discover -s tests` passes
- [ ] `python scripts/gen_docs.py` run, if a command's help or a helper's docstring changed

If it touches the engine side (`editor/`, `plugin/`, or a command that talks to the editor), in a project made from a
stock engine template:

- [ ] checked on 5.6
- [ ] checked on 5.7
- [ ] checked on 5.8
- [ ] windowed (a running editor)
- [ ] headless (`--headless`, or no editor running)

<!-- Say what you tried and what came back. For something that makes assets, view it: `ue.py render asset ...`. -->

## Docs

- [ ] The feature's page in `docs/` says what it was checked on and "What tripped up the first version"
- [ ] `CHANGELOG.md` has a line under "Unreleased"
