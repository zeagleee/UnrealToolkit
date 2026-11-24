# Contributing

Issues and pull requests are welcome. What's planned is in [TODO.md](TODO.md). Its section 4 lists small tasks that
don't need Unreal Engine installed (tests, CI, packaging, docs); open an issue or a draft pull request named after a
task's number (N1, N2...) to take it.

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

The engine-free code (`ue.py`, `uetk/` and `editor/scenario.py`) has tests that need only
Python 3.10 or newer, no Unreal Engine and no packages. From the toolkit folder:

```
python -m unittest discover -s tests                  # every test
python -m unittest discover -s tests -p test_state.py # one file
python -m compileall -q ue.py uetk editor scripts     # syntax of the editor-side code the tests can't import
```

Tests use the standard library's `unittest` and write only to temporary folders. Code in `editor/` imports `unreal`,
so the tests can't import it, except `editor/scenario.py`, which `tests/support.py` loads by path.

## Checking a change in the engine

Before a pull request that touches the engine side:

1. Make a project from a stock engine template (`scripts/new_from_template.py`), then `ue.py plugins install` and
   `ue.py build`.
2. Exercise the change in a running editor and headless, on every supported engine version you can (5.6, 5.7, 5.8). A
   C++ change must build on all of them; the engine's API differs between them (the README notes where).
3. Look at what you made: `ue.py render asset ...` or `ue.py mat preview ...` writes a PNG.

## Documentation

Each feature's README section ends with what it was checked on (versions, date, what was tried) and "What tripped up
the first version": the engine behaviour that cost time, so the next person doesn't pay it again. Keep both current.
General Unreal knowledge that isn't about the toolkit goes in [docs/unreal-notes.md](docs/unreal-notes.md).
