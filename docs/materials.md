# Materials

`ue.py mat` (`mat` in `ue.py py`; `editor/mat.py`) builds a material from a spec in which each output is an
expression, reads any material back as that same spec, and makes instances and material functions.

```json
{"params": {"Albedo": "/Game/Textures/T_Rock", "Tint": [1, 0.6, 0.4, 1], "Roughness": 0.6, "Tiling": 2},
 "let": {"tex_uv": "uv * Tiling"},
 "out": {"base_color": "sample(Albedo, tex_uv).rgb * Tint", "roughness": "Roughness", "metallic": 0}}
```

```
ue.py mat create /Game/Materials/M_Rock --spec rock.json --preview   # build (or rebuild in place), compile, save
ue.py mat describe /Game/Materials/M_Rock                            # the graph as spec text (--json: the spec itself)
ue.py mat instance /Game/Materials/MI_Rock_Red --parent /Game/Materials/M_Rock --set "Tint=[1,0.1,0.1]" Roughness=0.2
ue.py mat params /Game/Materials/MI_Rock_Red                         # every parameter, its value, overridden or not
ue.py mat preview /Game/Materials/M_Rock --mesh plane                # sphere (default), plane, cube or a mesh path
ue.py mat compile /Game/Materials/M_Rock                             # the compiler's errors and the nodes they came from
ue.py mat nodes noise                                                # node classes with their pins
```

- **Expressions are Python syntax.** Operators make Add, Multiply and the like; `x.rgb` uses the node's own channel
  outputs, or a mask. Names are parameters, `let`s and built-ins (`uv`, `time`, `world_pos`, `vertex_color`,
  `screen_uv`...). Functions cover the common nodes (`sample`, `lerp`, `fresnel`, `panner`, `switch`,
  `scene_texture`, `custom` for HLSL, `fn` for material functions), and any node class works by name
  (`RotateAboutAxis(...)`). The full list is in `editor/mat.py`'s docstring.
- **A number going into a node becomes that node's own constant** (Add's B, Lerp's Alpha), as when you type it in the
  details panel, not a separate Constant node. Repeated sub-expressions share one node.
- **Round trip:** `describe --json` gives a spec that `create` builds back into the same graph. Nodes used twice come
  back as `let`s. So: read a hand-made material, change a line, build it again.
- **Rebuilding** an existing material replaces its nodes in place (references to it stay valid) and first resets its
  settings to the engine's defaults, so the result depends only on the spec.
- **Texture samples** get their sampler type from the texture (sRGB colour, normal map, masks), as when you drop a
  texture on a node in the editor. `sample(T, uv, sampler='linear_color')` overrides it.
- **Material functions:** a spec with `"function": true`, `inputs` and `out`. Call one with
  `fn('/Game/MF_X', uv, 4).Result`.
- **Post-process materials** preview over a sphere and a cube that write custom depth and stencil value 1 (stencil
  needs `r.CustomDepth 3`). `render view|pie|actor --post` applies one to any render.
- **Needs the UETKEditor plugin** for `describe`, compile errors, and the outputs Python's `MaterialProperty` enum
  leaves out (`pixel_depth_offset`, `shading_model`, `clear_coat`, `clear_coat_roughness`, `displacement`,
  `customized_uv0`..`7`). On 5.8, building works without it. Before 5.8, Python's MaterialEditingLibrary can't list a
  material's nodes or a node's outputs, so building needs the plugin too.
- **Compile errors need a rendering editor.** A headless run (`-nullrhi`) compiles no shaders, so there `errors` is
  `null` with a note, not `[]`. Check with `ue.py mat compile PATH` in a running editor.
- **Checked on UE 5.8 (2026-11-24):**
  - Built, compiled and previewed: a textured PBR material, an instance with overrides, a post-process outline from
    custom stencil, an unlit translucent material with Custom HLSL and fresnel, and a material function with a
    material that calls it.
  - A 44-node material using most of the syntax built cleanly, and `describe` rebuilt it identically. So did the
    engine's WorldGridMaterial, BasicShapeMaterial and DefaultDeferredDecalMaterial.
  - A float2 + float3 came back as the compiler's own message, naming the node.
- **Checked on UE 5.6 and 5.7 (2026-11-24):** the plugin builds. Building, `describe` round trips, instances,
  functions and calls work headless. In a running editor, compile errors came back (a type error, and an undeclared
  name in Custom HLSL with the shader compiler's line), and previews rendered.
- **A material with pixel depth offset can vanish in a steep `--mesh plane` preview:** the offset pushes its depth
  behind the preview floor. Use `--pitch -20` or `floor=False`.
- **What tripped up the first version:**
  - `MaterialEditingLibrary.delete_all_material_expressions` (and `..._in_function`) removes nodes from the list it
    loops over, so it deletes only about half. `mat` deletes from a copy until none are left.
  - `set_material_instance_*_parameter_value` always returns False, even when it worked (5.8's source). `mat.instance`
    checks by reading the value back.
  - `connect_material_expressions` takes pin names. An empty name is the first pin, and unnamed outputs match
    "R"/"G"/"B"/"A" by their channel mask. Many single-input nodes report their input as "None".
  - When one node feeds another twice through different outputs (`lerp(t.r, t.g, x)`),
    `get_input_node_output_name_for_material_expression` returns the first match. Reading graphs goes through the
    plugin for that reason.
  - The editor's Sine and Cosine nodes have a period of 1 by default (`sin(2πx)`); `sin(x)` in a spec sets the
    period to 0, which is radians.
  - `unreal.Vector4f()` takes no constructor arguments; set x, y, z and w with `set_editor_property`.
  - UFUNCTIONs of C++ libraries are snake_case in Python (`DescribeGraph` is `describe_graph`).
