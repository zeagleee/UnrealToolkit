# Enhanced Input

`ue.py input` (`inputs` in `ue.py py`; `editor/inputs.py`) reads and writes Enhanced Input assets as one spec:
actions with their value types, triggers and modifiers, and mapping contexts with each action's keys.

```json
{"actions": {"/Game/Input/Actions/IA_Sprint": {"type": "bool", "description": "Run while held"}},
 "contexts": {"/Game/Input/IMC_Default": {"mappings": {"IA_Sprint": ["LeftShift", "Gamepad_LeftThumbstick"]}}}}
```

```
ue.py input dump                                   # every context in /Game (and what uses it), every action
ue.py input dump /Game/Input/IMC_Default --json    # one context as a spec that `create` takes back
ue.py input create --spec input.json               # create or update actions and contexts
ue.py input map /Game/Input/IMC_Default IA_Sprint LeftShift --trigger hold
ue.py input unmap /Game/Input/IMC_Default --action IA_Sprint                # or --key LeftShift, or both
ue.py input use /Game/Blueprints/BP_PlayerController /Game/Input/IMC_Default # its default contexts (--remove)
ue.py input keys gamepad                           # key names
ue.py input active                                 # the running game's player: active contexts; add / remove CONTEXT
```

- **A context lists keys per action.** Each action listed gets exactly those keys; the context's other actions keep
  theirs (`"replace": true` clears them). Triggers and modifiers are names, or `{name: {property: value}}`:
  `{"hold": {"hold_time_threshold": 0.5}}`, `{"chord": {"chord_action": "IA_Aim"}}`, `{"swizzle": {"order": "zyx"}}`.
  The full format is in `editor/inputs.py`'s docstring.
- **Actions are named by asset name** when only one InputAction has it, otherwise by path. `dump` writes them the same
  way, so its output always builds back.
- **Blueprint logic for an action:** `g.input_action('IA_Sprint')` in `bpedit` adds the action's event node (see
  [Editing Blueprints](editing-blueprints.md)).
- **In the running game:** `inputs.active()` lists the contexts a player has, with priorities, and
  `add_context`/`remove_context` change them. Drive keys with `ue.py press` or `ueh.key_down`: they go through the
  mappings, so triggers and modifiers run.
- **Checked on UE 5.8 (2026-11-24), in a fresh Third Person template:**
  - `dump` read the template's five contexts and eleven actions, with where each context is used and one action no
    context maps.
  - Hold-to-sprint (Left Shift, left-stick click) and hold-to-walk-slowly (Left Alt, left shoulder) went in with
    `input create` and two `input_action` events setting `MaxWalkSpeed`, without the editor UI. In PIE, with forced
    keys: 500 walking, 1000 sprinting, 200 slow, on keyboard and on gamepad.
  - A context using chord, hold, pulse, dead zone, scalar, swizzle and negate settings read back identically after a
    rebuild.
- **Checked on 5.6 and 5.7 (headless):** the same build and round trip, map and unmap, the key list, and an input
  action event compiled into a Blueprint.
- **What tripped up the first version:**
  - 5.6 keeps a context's mappings in `Mappings`; 5.7 moved them into `DefaultKeyMappings.Mappings`. `inputs` handles
    both. Per-profile overrides (`MappingProfileOverrides`, 5.7+) aren't read or written yet.
  - Before 5.8 a chord trigger's `ChordAction` is edit-on-instance-only, and Python refuses to set it ("cannot be
    edited on templates"). `uprop.set_prop` falls back to the plugin's text import.
  - Python returns `EnhancedInputLocalPlayerSubsystem.has_mapping_context`'s out-parameter, not its bool: the priority
    when the context is applied, None when not. Priority 0 is falsy.
  - When two active contexts map the same key, the first action to take it consumes it. A test context that also
    mapped `Gamepad_Left2D` silently stopped the stick from moving the character.
  - `EditorAssetLibrary` refuses to run during PIE, so `inputs` loads assets with `unreal.load_object` and finds them
    through the asset registry.
  - `ueh.prop_specs` (and `ueh.props`) skipped each property that followed one with an empty description: the
    docstring regex's `\s*` crossed the line break. `InputModifierNegate` showed x and z but not y.
  - Python has no list of key names; the plugin reads them from `EKeys` (`ue.py input keys`).
