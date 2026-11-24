# Instructions for agents

A block to paste into a project's `CLAUDE.md` or `AGENTS.md`, so a coding agent knows the toolkit is there and how to
use it without reading the whole README first. Replace `C:/Tools/UnrealToolkit` with where you cloned it (or use `ue`
in place of `python C:/Tools/UnrealToolkit/ue.py` if you installed it with pipx).

The same text is a Claude Code skill in [`skills/unreal-toolkit/SKILL.md`](../skills/unreal-toolkit/SKILL.md): copy
that folder into the project's `.claude/skills/` (or `~/.claude/skills/` for every project).

---

```markdown
## Unreal Engine: drive it with UnrealToolkit

Drive the editor and the game with `python C:/Tools/UnrealToolkit/ue.py` (below: `ue.py`), run from the project
folder. Never click in the editor or send keystrokes to it. `ue.py -h` and `ue.py <command> -h` list every option;
the guides are in C:/Tools/UnrealToolkit/docs/ and every command is in docs/commands.md.

Reach for these first:
- State: `ue.py info`, `ue.py editor status`, `ue.py log --errors`, `ue.py crash`.
- Compile: `ue.py build` (editor closed); `ue.py livecoding` for .cpp-only changes with the editor open;
  `ue.py build --restart` after header, UPROPERTY, UFUNCTION or new-class changes (add `--save` or `--discard` if
  assets are dirty).
- Editor: `ue.py editor start` (builds first, waits until usable), `ue.py editor stop`.
- Python in the live editor: `ue.py py "ueh.level()"`, `ue.py py -f script.py`. `unreal` and `ueh` are
  pre-imported; `ueh.help()` lists the helpers. The last expression is printed as JSON. `--headless` runs without
  an open editor.
- Waiting for the game: a top-level `yield ueh.wait(5)` or `yield ueh.until(lambda: ...)` in a `py` script makes it
  a scenario that runs across frames. Don't chain `py` calls with sleeps.
- Play: `ue.py pie start --protect` / `pie stop`; real input through Enhanced Input with `ue.py press KEY --hold 0.5`
  and `ue.py mouse DX DY`; menus with `ue.py ui tree`, `ui click LABEL`.
- Look at things: `ue.py render view|pie|actor|asset|widget` writes a PNG; open it to check your work.
- Content: `ue.py mat`, `ue.py input`, `ue.py import`, `ue.py ui create`, `ue.py vfx` build assets from JSON specs,
  and `describe` reads any asset back as a spec. Blueprints: `ue.py bpdump` (all as text), `bpedit` in `ue.py py`.
- Checks: `ue.py validate` (exit code 1 on errors), `ue.py test` (automation tests, headless).
- Multiplayer: `ue.py net start --clients 2`, then `ue.py --instance client1 py ...`; `ue.py net stop`.

Rules of thumb:
- Nothing is saved unless you save it (`save=True`, `ueh.save()`, `ue.py save`). Check `ueh.dirty()` before
  stopping or killing the editor, and never discard someone's unsaved work without asking.
- Prefer `ue.py editor stop` to `ue.py kill`: a graceful exit writes caches, so the next start is faster.
- PIE runs can overwrite save games: start with `ue.py pie start --protect`, and `pie stop` puts them back.
- Cvars set with `ue.py exec` stay set until changed back or the editor restarts; put them back.
- Paths on Unreal command lines use forward slashes.
- Read the `--- log ---` lines after a `py` call: Blueprint compile errors and engine warnings show up there.
- Many commands need the toolkit's editor plugins: `ue.py plugins install`, then `ue.py build --restart`. A warning
  that a plugin "is older than the toolkit's" means run those two again.
- A modal dialog stops the editor: when a call times out, run `ue.py windows` and `ue.py screenshot`, and ask the
  user to answer it. Don't send keys or clicks to it.
- The bridge runs any Python it is given, with the editor's rights. Only use it on your own project.
```
