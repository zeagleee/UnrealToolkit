# Spawning into the running game

`ueh.spawn(..., world='game')` spawns into PIE or a `ue.py net` game process (in a game process it's the default),
with properties set before the actor's construction script and BeginPlay run. It needs the UETKEditor plugin.

```python
ueh.spawn('/Game/BP_Pickup', (300, 0, 120), world='game', props={'Amount': 5, 'Kind': 'gold'}, label='Coin')
ueh.spawn('/Game/BP_Hat', (0, 0, 90), world='game', attach_to=pawn, scale=(0.5, 0.5, 0.5))  # keeps its world spot
ueh.actors(cls='BP_Pickup', world='game'); ueh.destroy('Coin', world='game')
```

```
ue.py --instance server py "ueh.spawn('/Game/BP_Pickup', (300, 0, 120), props={'Amount': 5})"   # clients see it
```

- **`props`** takes any property, Blueprint variables included, with the same loose values as the rest of the toolkit
  (lists for vectors and colours, enum names, asset paths). Expose-on-spawn variables are just the usual case.
- **On a net server,** spawn there: replicated actors and their replicated variables reach the clients.
- **Checked on UE 5.8 (2026-11-24):**
  - In PIE, a Blueprint spawned with two expose-on-spawn values, and its construction script saw them.
  - A second actor attached to the player's character and followed it.
  - On a `ue.py net` listen server, the same Blueprint spawned with `--instance server`, and the client saw a simulated
    proxy carrying the replicated values, the one set in the construction script included.
- **Checked on 5.6 and 5.7:** the same PIE test; on 5.7 also a net server's spawn, seen on the client with its
  replicated values.
- **What tripped up the first version:** Python can't spawn deferred (`BeginDeferredActorSpawnFromClass` and
  `FinishSpawningActor` aren't exposed), and setting properties after a normal spawn is too late for the construction
  script and BeginPlay. The plugin's `UETKGameLibrary.begin_spawn` / `finish_spawn` split the spawn around them.
