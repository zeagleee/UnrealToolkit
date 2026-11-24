# Menus and HUD (UMG)

`ue.py ui` (`ui` in `ue.py py`; `editor/ui.py`) reads and drives the widgets on screen in the running game (PIE, or a
`ue.py net` process with `--instance`), and builds Widget Blueprints from a spec. It needs the UETKEditor plugin.

```
ue.py press P                               # open a menu the way a player would (or: ue.py ui open /Game/UI/WBP_Pause)
ue.py ui wait Paused                        # until something labelled "Paused" is on screen
ue.py ui tree                               # the widgets on screen: class, name, text, value, disabled
ue.py ui click "Save game"                  # a button or checkbox, by its label (ignoring case) or name
ue.py ui type PlayerName Ada                # an editable text: set, changed, committed with Enter
ue.py ui set Volume 0.3                     # a slider, spin box, checkbox or combo box
ue.py ui text Status                        # one widget's text; `ui text` alone: all the text on screen
ue.py ui click Resume && ue.py ui wait Paused --gone
ue.py ui create /Game/UI/WBP_Pause --spec pause.json    # a Widget Blueprint's designer tree from a spec
ue.py ui describe /Game/UI/WBP_Pause                    # read one back
```

In scenarios: `yield ui.wait('Paused')`, then `ui.click('Resume')`.

- **Why it's needed:** forced keys reach gameplay input, not a focused widget, so menus need their own driver. Clicks,
  typing and values go through the widgets' own events (a click fires OnPressed, OnReleased and OnClicked), so
  Blueprint and C++ handlers run as they do for a real click. Slate's hit testing doesn't run: a button behind another
  widget still gets clicked.
- **Targets** match a widget's name or its label (its own text, or the text inside it): exactly first, then the start
  of the label, then anywhere in it. A button and the text inside it share a label; the button wins. A missing or
  ambiguous target lists what is on screen.
- **Building a menu:** the spec nests widgets (`type`, `name`, `text`, `props`, `slot`, `children`), and every widget
  becomes a variable. Wire it with `bpedit`: `g.bound_event('ResumeButton', 'OnClicked')`. The format is in
  `editor/ui.py`'s docstring.
- **Checked on UE 5.8 (2026-11-24), in a Third Person template, with the editor in the background:**
  - A pause menu was built from a spec: a title, two buttons, a checkbox, a slider, a name field and a status line.
    Its events were wired with `bound_event`, and an `IA_Pause` action on P opened it from the character Blueprint.
  - In PIE, `press P` opened it and paused the game. Typing a name, and clicking Save, changed the status line through
    the Blueprint. The checkbox and slider took their new values. Resume closed the menu and unpaused the game.
  - The same in a `ue.py net` client process, with `--instance client1`.
- **Checked on 5.6 and 5.7:** the plugin builds on both. The same menu was built (on 5.7 headless), opened with
  `ui.open` in PIE, and driven the same way.
- **Not yet:**
  - Rectangles: `tree` shows a widget's position and size in viewport pixels only when Slate has arranged it, and in
    no run here (editor in the background, a net client's window) had it, so none were shown.
  - The entries a ListView or TileView generates aren't walked.
- **What tripped up the first version:**
  - Python can't reach a user widget's or Widget Blueprint's `WidgetTree`, or create a widget (Create Widget is
    internal to Blueprint). The plugin's `UETKWidgetLibrary` does those, and Python walks the rest
    (`PanelWidget.get_child_at`).
  - A widget added to a Widget Blueprint must be registered with `UWidgetBlueprint::OnVariableAdded`, as the designer
    does. Without it the compiler ensures for every widget ("Widget was added but did not get a GUID").
  - Marking the Blueprint structurally modified after each added widget recompiles its skeleton every time (about 3 s
    per widget). Mark it modified, and compile once at the end.
  - Struct properties compared through `str()` always differ: the text includes the struct's address. `uprop`
    compares them by `export_text()`.
  - In Python, `UWidgetBlueprintLibrary` is `unreal.WidgetLibrary` and `USlateBlueprintLibrary` is
    `unreal.SlateLibrary`.
  - `BlueprintEditorLibrary.get_blueprint_parent_class` exists from 5.8; the asset registry's `ParentClass` tag has it
    on every version.
