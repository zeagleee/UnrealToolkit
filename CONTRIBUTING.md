# Contributing

Issues and pull requests are welcome. Questions and ideas go to
[Discussions](https://github.com/zeagleee/UnrealToolkit/discussions). Everyone taking part follows the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Finding something to work on

Tasks ready to take are issues:

- [`good first issue`](https://github.com/zeagleee/UnrealToolkit/labels/good%20first%20issue): small, and need only
  Python 3 and git.
- [`help wanted`](https://github.com/zeagleee/UnrealToolkit/labels/help%20wanted): bigger, or need a decision first.
- [`needs engine`](https://github.com/zeagleee/UnrealToolkit/labels/needs%20engine): need Unreal Engine installed, to
  do and to check.

Comment on an issue to take it, so nobody else starts the same thing, and name its task number (N23, N24...) in your
pull request. Everything planned is in [TODO.md](TODO.md); its section 4 lists the tasks that don't need Unreal Engine.
For something that isn't there yet, open an issue or a discussion first.

## Reporting a problem

Give the engine version (`ue.py info` prints it), the command you ran, and what came back. The editor's log helps:
`ue.py log --errors`, or the headless run's log under `<Project>/Saved/UnrealToolkit/headless/`.

## How features are built here

- **Python first.** System-side code (`ue.py`, `uetk/`) uses only the standard library. Editor-side code (`editor/`)
  runs in Unreal's Python. C++ (`plugin/`) is for what Python can't reach, and stays optional where it can.
- **Spec in, describe out.** A feature that builds assets takes a spec (a dict or JSON) and has a `describe` that reads
  any asset of that kind back into the same spec, so `build(describe(x))` reproduces `x`. Building an existing asset
  updates it in place, so references to it stay valid.
- **Headless where the engine allows it** (`ue.py py --headless`), and errors a headless run can't survive are caught
  before they reach the engine.
- **Clear errors.** Say what was wrong and what would be right ("no key named 'Spcebar'; did you mean SpaceBar").

## Running the tests

The engine-free code (`ue.py`, `uetk/`, `editor/scenario.py`, `scripts/gen_docs.py`) has tests that need only
Python 3.10 or newer, no Unreal Engine and no packages. From the toolkit folder:

```
python -m unittest discover -s tests                  # every test
python -m unittest discover -s tests -p test_state.py # one file
python -m compileall -q ue.py uetk editor scripts     # syntax of the editor-side code the tests can't import
python scripts/gen_docs.py                            # after changing a command's help or a helper's docstring
```

Tests use the standard library's `unittest` and write only to temporary folders. Code in `editor/` imports `unreal`,
so the tests can't import it, except `editor/scenario.py`, which `tests/support.py` loads by path. GitHub Actions
runs all of this on Windows and Linux with Python 3.10 and 3.13 on every push and pull request
(`.github/workflows/tests.yml`), and fails when `docs/commands.md` or `docs/helpers.md` is out of date.

## Checking a change in the engine

Before a pull request that touches the engine side:

1. Make a project from a stock engine template (`scripts/new_from_template.py`), then `ue.py plugins install` and
   `ue.py build`.
2. Exercise the change in a running editor and headless, on every supported engine version you can (5.6, 5.7, 5.8). A
   C++ change must build on all of them; the engine's API differs between them (the feature pages in `docs/` note
   where).
3. Look at what you made: `ue.py render asset ...` or `ue.py mat preview ...` writes a PNG.

## Documentation

Each feature's page in `docs/` (linked from the README's "Guides") ends with what it was checked on (versions, date, what was tried) and "What tripped up
the first version": the engine behaviour that cost time, so the next person doesn't pay it again. Keep both current.
General Unreal knowledge that isn't about the toolkit goes in [docs/unreal-notes.md](docs/unreal-notes.md).

## Releasing

For the maintainer:

1. In `CHANGELOG.md`, "Unreleased" becomes "X.Y.Z (date)", with a new empty "Unreleased" above it, and
   `__version__` in `uetk/__init__.py` becomes the same version (a test checks that they match). Merge that.
2. Tag the merge commit `vX.Y.Z`, push the tag, and publish a GitHub release from it with that version's changelog
   section as its notes: `gh release create vX.Y.Z --notes-file notes.md --discussion-category Announcements`.
3. Publishing the release runs `.github/workflows/publish.yml`. It builds the sdist and wheel, checks them with
   `twine check`, fails if the version doesn't match the tag, and attaches them to the release. It uploads them to
   PyPI only when the repository variable `PYPI_PUBLISH` is `true`.

Turning PyPI uploads on, once: on pypi.org, under Publishing, add a pending trusted publisher (project
`unreal-toolkit`, owner `zeagleee`, repository `UnrealToolkit`, workflow `publish.yml`, environment `pypi`). Then
`gh variable set PYPI_PUBLISH --body true`, and upload the latest release with
`gh workflow run publish.yml --ref vX.Y.Z`.
