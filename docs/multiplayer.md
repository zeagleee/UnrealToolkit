# Multiplayer sessions

`ue.py net start` runs a listen server (or `--dedicated` server) and N clients as separate game processes. Each is
`UnrealEditor.exe <project> -game|-server`, headless (`-nullrhi`, `-nosound`, `-nosteam`) unless named in `--render`.
Each gets its own bridge (`-UETKBridge=<name>`), so any command can target one with `--instance`:

```
ue.py net start --map /Game/Maps/Lvl_Arena --clients 2      # waits for every bridge and for the clients to join
ue.py --instance client1 py "ueh.net_info()"               # {role, map, players, time}; exec, wait and render work too
ue.py --instance client2 exec "MyCheat 1"
ue.py net status                                            # role, map, players and game time of each process
ue.py net logs --errors                                     # every process's log merged by timestamp, name-prefixed
ue.py net run my_test.py                                    # a test script with `net` in scope; scripts/net_smoke_test.py
ue.py net stop                                              # kills them all; --protect on start restores SaveGames
```

- **Why separate processes:** in-process multi-player PIE can't change maps ([Online sessions](unreal-notes.md#online-sessions-travel-and-settings)), and its players
  share one Python interpreter. These are real client/server processes, connected over IP on `127.0.0.1:<port>`.
- **In a game process, `ueh` runs against the game world.** `ueh.game_world()`, `player()`, `console()`, scenario
  waits and `render_pie` all work there, and `ueh.is_editor()` is False. Editor-only helpers (level editing, assets)
  don't apply.
- **`--render client1`** gives that client a 960x540 window, so `ue.py --instance client1 render pie` shows what it
  sees. Keep the rest headless: several rendering game processes beside an editor exhausted a 16 GB machine
  ([Online sessions](unreal-notes.md#online-sessions-travel-and-settings)).
- **Steam:** off by default (`-nosteam`: Null subsystem, direct IP). `--steam` leaves it on, but two processes on one
  PC can't both be the same Steam user.
- **Dedicated servers run no Slate**, which is what Python's tick hooks (and so the bridge) use. There the bridge ticks
  from the UETKEditor plugin's engine-loop ticker (`unreal.UETKTicker`), so install the plugins first.
- `procs` now tells these `game` processes apart from the editor, so `editor stop`/`start` leave them alone;
  `ue.py kill` still kills everything of the project.
- **Changing `agent_bridge.py` while editors run:** each bridge hot-reloads when its source changes. A reload of a
  half-finished edit once left an editor without a bridge (a `NameError`). It now keeps the old bridge and retries at
  the next change.
