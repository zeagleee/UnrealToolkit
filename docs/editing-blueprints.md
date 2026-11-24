# Editing Blueprints

`bpedit` (pre-imported in `ue.py py`; UETKEditor plugin) is the write side of `bpdump`. It edits graphs the way the
editor does: nodes come from the editor's node creators, wires go through the K2 schema (which inserts conversion
nodes), literals are validated by it, and `compile` returns the compiler's own messages.

```python
g = bpedit.graph('/Game/BP_Door')                   # the event graph; graph(path, 'MyFunction') for a function
begin = g.event('BeginPlay')                        # the existing override (a ghost one is enabled), or a new one
say = g.call('PrintString', InString='Ready')       # pin literals as keywords
g.chain(begin, say)                                 # exec wires through nodes in order, laid out left to right
bpedit.add_variable('/Game/BP_Door', 'Health', 'float', 100, editable=True)
hit = g.custom_event('TakeHit', Amount='float')
minus = g.call('Subtract_DoubleDouble')
g.link(g.get('Health'), 'Health', minus, 'A'); g.link(hit, 'Amount', minus, 'B')
set_hp = g.set_var('Health'); g.link(minus, 'ReturnValue', set_hp, 'Health'); g.chain(hit, set_hp)
g.tidy()                                            # data nodes to the left of what uses them
bpedit.compile('/Game/BP_Door'); ueh.save('/Game/BP_Door')
```

- **To find what to edit:** `ue.py bp graph PATH [GRAPH]` (or `g.text()`) lists every node with its id, title,
  position, function, literals and links (`K2Node_CallFunction_2.execute`). `ue.py bp graphs PATH` lists the graphs.
  Address nodes by id or by a unique title, and pins by name or display name: `True`/`False` on a Branch,
  `ReturnValue`, `In String`. A Set node's value output is its variable's name, or `value`.
- **Node kinds:** `call` (any Blueprint-callable function: the Blueprint's own, Kismet libraries, GameplayStatics, or
  `cls='Character'` for others; `DestroyActor` finds `K2_DestroyActor`), `event`, `custom_event` (with typed
  parameters), `get`/`set_var`, `branch`, `sequence`, `cast`, `macro` (StandardMacros: ForEachLoop, DoOnce, Gate...),
  `self_ref`, `comment`, `input_action` (an Enhanced Input action event: Started, Triggered, Completed... and
  ActionValue), `bound_event` (a widget's or component's event, as the editor's "+" buttons add it:
  `bound_event('ResumeButton', 'OnClicked')`, `bound_event('TriggerBox', 'OnComponentBeginOverlap')`), and `any_node` (any graph node class, its properties set before its pins are made:
  `any_node('K2Node_EnhancedInputAction', InputAction='/Game/Input/IA_Jump')`, which is what `input_action` does).
  Also `add_variable` (category, instance editable,
  replicated, expose on spawn) and `add_function` (typed inputs and outputs, pure).
- **Rewiring:** `link` on an exec output replaces its link (exec outputs carry one), which is how you insert a node
  between two others: link A to new, then new to B. `unlink` breaks one link or all of a pin's links; `remove` deletes
  a node.
- **Checked against the template project (2026-11-23):**
  - A Blueprint built from nothing (BeginPlay print, a `TakeHit` event, health math, a branch, DestroyActor)
    compiled and behaved correctly in PIE.
  - `bpdump` read it back as `Health = Subtract(Health, Amount); if (Health <= 0) DestroyActor()`.
  - Edits to the template's jump pad worked: a literal, and a node inserted between a cast and a call.
  - A bool wired to a vector was refused with the schema's reason, and a missing target came back as the compiler's
    message.
- **What tripped up the first version:**
  - A removed node stays outered to its graph until garbage collection, so exports (`bpdump`) still listed it.
    `remove` now renames it into the transient package.
  - EditorAssetLibrary refuses to run during PIE, so `ueh.cls('/Game/BP_X')` now loads `/Game/BP_X.BP_X_C` directly.
