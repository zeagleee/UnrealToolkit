# Contributing

Issues and pull requests are welcome. What's planned is in [TODO.md](TODO.md).

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

## Checking a change

There's no automated test suite yet (see TODO.md, "Tests"). Before a pull request:

1. Make a project from a stock engine template (`scripts/new_from_template.py`), then `ue.py plugins install` and
   `ue.py build`.
2. Exercise the change in a running editor and headless, on every supported engine version you can (5.6, 5.7, 5.8). A
   C++ change must build on all of them; the engine's API differs between them (the README notes where).
3. Look at what you made: `ue.py render asset ...` or `ue.py mat preview ...` writes a PNG.

## Documentation

Each feature's README section ends with what it was checked on (versions, date, what was tried) and "What tripped up
the first version": the engine behaviour that cost time, so the next person doesn't pay it again. Keep both current.
General Unreal knowledge that isn't about the toolkit goes in [docs/unreal-notes.md](docs/unreal-notes.md).
