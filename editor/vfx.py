"""vfx - build Niagara particle systems from Python. Runs inside UnrealEditor; pre-imported as `vfx` in `ue.py py`.

Needs the UETKVfx editor plugin in the project: `ue.py vfx install`, then `ue.py build --restart`.

    vfx.presets()                                        # preset names, what they make, their parameters
    vfx.create('/Game/VFX/NS_Fire', 'fire', color=(1, .35, .08), scale=1.5)   # build + compile + save
    vfx.preview('/Game/VFX/NS_Fire')                     # PNG contact sheet, rendered offscreen (level untouched)
    vfx.describe('/Game/VFX/NS_Fire')                    # the stack as readable text
    vfx.build(spec, '/Game/VFX/NS_Custom')               # from a spec dict (SPEC below); presets return these
    vfx.place('/Game/VFX/NS_Fire', (0, 0, 100))          # put it in the open level (marks the level dirty)

Lower level (same addressing as the C++ library; names ignore case and spaces):
    s = vfx.new_system(path); vfx.add_emitter(s, 'Sparks'); vfx.add_module(s, 'Sparks', 'particle_update', 'gravity')
    vfx.set_input(s, 'Sparks', 'particle_spawn', 'Initialize Particle', 'Lifetime', {'random': [1, 2]})
    vfx.input(s, ...) shows one input; vfx.modules_in(s, emitter, stage) lists a stage; vfx.compile(s); vfx.save(s)

Input values (set_input and every spec field that takes a value):
    1.5 / True / 'Random' / (1, 2, 3)      local value (numbers, bools, enum names, vectors, colors)
    {'random': [a, b]}                     uniform random range (dynamic input chosen by the input's type)
    {'curve': [[t, v...], ...], 'interp': 'cubic'}   curve over the particle's normalized age (or 'index': param)
    {'link': 'Particles.NormalizedAge'}    read a parameter (User.X, Emitter.Age, ...)
    {'expression': 'Particles.NormalizedAge * 2'}
    {'dynamic': '/Niagara/DynamicInputs/...', 'inputs': {'Child': value}}
    {'object': '/Game/Meshes/SM_Rock'}     object-asset inputs
    {'reset': True}                        back to the module default

SPEC (all keys optional unless noted; see vfx_presets.py for working examples):
    {'loop': 'once'|'infinite'|'multiple', 'duration': 2, 'loop_count': 3, 'warmup': 0,
     'preview': {'times': [.1, .5], 'zoom': 2, 'pitch': -10},   # defaults for create(..., preview_after=True)
     'user': {'Tint': ['color', [1, .5, .2, 1]]},           # user parameters (link to them with {'link': 'User.Tint'})
     'emitters': [{
        'name': 'Sparks',                                    # required
        'template': 'Minimal',                               # /Niagara/DefaultAssets/Templates/Emitters/<name>, or a path
        'sim': 'cpu'|'gpu', 'local_space': False, 'bounds': [[-500,-500,-100], [500,500,800]],
        'properties': {'RandomSeed': '7'},                   # any FVersionedNiagaraEmitterData field, as text
        'loop': 'once'|'infinite'|'multiple', 'duration': 1, 'loop_count': 2, 'delay': 0.2,   # emitter's own cycle
        'spawn': {'rate': 30} | {'burst': 40, 'time': 0} | {'bursts': [[40, 0], [20, .5]]} | {'per_unit': 10},
        'lifetime': 1.5 | [1, 2],
        'color': [r, g, b, a] | {'random': [[...], [...]], 'channels': 'rgb_a'|'rgba'|'individual'},
        'size': 8 | [4, 12] | {'xy': [2, 30]} | {'xy_random': [[1, 10], [3, 30]]},
        'mass': 1 | [1, 3], 'rotation': 'random' | 45, 'mesh_scale': 1 | [.5, 1.5], 'ribbon_width': 10,
        'shape': {'sphere': 50} | {'box': [x, y, z]} | {'plane': [x, y]} | {'cylinder': [radius, height]}
                 | {'ring': radius} | {'cone': [length, angle]} | {'torus': [radius, tube]} | {'point': True},
        'velocity': [x, y, z] | {'random': [[...], [...]]} | {'cone': 30, 'axis': [0, 0, 1], 'speed': [200, 400]}
                    | {'point': [100, 300]}    (outward from the emitter),
        'gravity': -980 | [x, y, z], 'drag': 2, 'curl_noise': 80 | {'strength': 80, 'frequency': 40},
        'vortex': 300 | {'amount': 300, 'axis': [0, 0, 1], 'pull': 50}, 'attract': {'strength': 200, 'radius': 300},
        'wind': [x, y, z], 'acceleration': [x, y, z],
        'color_over_life': [[t, r, g, b, a], ...],          # multiplies the initial color
        'alpha_over_life': [[t, a], ...],  'size_over_life': [[t, scale], ...],
        'rotation_rate': 90 | [-180, 180],                   # sprite spin, degrees per second
        'subuv': 'random' | 'linear' | 'loop',               # flipbook frame choice (renderer 'subimages')
        'collision': True | {'restitution': .4, 'friction': .2},
        'modules': [{'stage': 'particle_update', 'module': 'vortex', 'before': 'Solve Forces and Velocity',
                     'inputs': {'Vortex Force Amount': 100}}],
        'inputs': [{'stage': 'particle_spawn', 'module': 'Initialize Particle', 'input': 'Mass', 'value': 2}],
        'renderer': {'type': 'sprite', 'material': 'glow', 'alignment': 'velocity', 'facing': 'camera',
                     'subimages': [4, 4], 'sort': 'view_depth', 'props': {'PivotInUVSpace': '(X=0.5,Y=1)'}}
                    | {'type': 'ribbon', 'material': 'ribbon'} | {'type': 'mesh', 'mesh': '/Engine/BasicShapes/Cube'}
                    | {'type': 'light', 'radius': 300, 'intensity': 2},   # a light per particle, particle colour
        'renderers': [...several...]}]}
    Materials: 'glow', 'smoke', 'ribbon', 'mesh', 'shockwave' (generated under /Game/VFX/Shared) or any material path.
"""
import json
import os

import unreal

import vfx_assets

EAL = unreal.EditorAssetLibrary
TEMPLATES = "/Niagara/DefaultAssets/Templates/Emitters/"
STAGES = ("system_spawn", "system_update", "emitter_spawn", "emitter_update", "particle_spawn", "particle_update")
SOLVER = "Solve Forces and Velocity"

# Short names for module scripts (any full /Path also works).
MODULES = {
    "emitter_state": "/Niagara/Modules/Emitter/EmitterState",
    "spawn_rate": "/Niagara/Modules/Emitter/SpawnRate",
    "spawn_burst": "/Niagara/Modules/Emitter/SpawnBurst_Instantaneous",
    "spawn_per_unit": "/Niagara/Modules/Emitter/SpawnPerUnit",
    "initialize_particle": "/Niagara/Modules/Spawn/Initialization/V2/InitializeParticle",
    "initialize_ribbon": "/Niagara/Modules/Spawn/Initialization/InitializeRibbon",
    "shape_location": "/Niagara/Modules/Spawn/Location/V2/ShapeLocation",
    "add_velocity": "/Niagara/Modules/Spawn/Velocity/AddVelocity",
    "add_velocity_in_cone": "/Niagara/Modules/Spawn/Velocity/AddVelocityInCone",
    "initial_mesh_orientation": "/Niagara/Modules/Spawn/Orientation/V3/InitialMeshOrientation",
    "particle_state": "/Niagara/Modules/Update/Lifetime/ParticleState",
    "gravity": "/Niagara/Modules/Update/Forces/GravityForce",
    "drag": "/Niagara/Modules/Update/Forces/Drag",
    "curl_noise": "/Niagara/Modules/Update/Forces/V2/CurlNoiseForce",
    "vortex": "/Niagara/Modules/Update/Forces/VortexForce",
    "point_attraction": "/Niagara/Modules/Update/Forces/PointAttractionForce",
    "wind": "/Niagara/Modules/Update/Forces/WindForce",
    "acceleration": "/Niagara/Modules/Update/Forces/AccelerationForce",
    "solver": "/Niagara/Modules/Solvers/SolveForcesAndVelocity",
    "scale_color": "/Niagara/Modules/Update/Color/ScaleColor",
    "color": "/Niagara/Modules/Update/Color/Color",
    "scale_sprite_size": "/Niagara/Modules/Update/Size/ScaleSpriteSize",
    "scale_mesh_size": "/Niagara/Modules/Update/Size/ScaleMeshSize",
    "scale_ribbon_width": "/Niagara/Modules/Ribbons/ScaleRibbonWidth",
    "sprite_rotation_rate": "/Niagara/Modules/Update/Orientation/SpriteRotationRate",
    "mesh_rotation_rate": "/Niagara/Modules/Update/Orientation/MeshRotationRate",
    "subuv": "/Niagara/Modules/Update/SubUV/V2/SubUVAnimation",
    "collision": "/Niagara/Modules/Collision/Collision",
    "light_attributes": "/Niagara/Modules/Light/Light_Attributes",
}

# Dynamic inputs by purpose and input type.
_RANDOM = {
    "NiagaraFloat": "/Niagara/DynamicInputs/UniformRange/UniformRangedFloat",
    "NiagaraInt32": "/Niagara/DynamicInputs/UniformRange/UniformRangedInt",
    "Vector2f": "/Niagara/DynamicInputs/UniformRange/UniformRangedVector2D",
    "Vector3f": "/Niagara/DynamicInputs/UniformRange/UniformRangedVector",
    "Vector4f": "/Niagara/DynamicInputs/UniformRange/UniformRangedVector4",
    "LinearColor": "/Niagara/DynamicInputs/UniformRange/UniformRangedLinearColor",
}
_CURVE = {
    "NiagaraFloat": "/Niagara/DynamicInputs/ValueFromCurve/FloatFromCurve",
    "Vector2f": "/Niagara/DynamicInputs/ValueFromCurve/Vector2DFromCurve",
    "Vector3f": "/Niagara/DynamicInputs/ValueFromCurve/VectorFromCurve",
    "Vector4f": "/Niagara/DynamicInputs/ValueFromCurve/Vector4FromCurve",
    "LinearColor": "/Niagara/DynamicInputs/ValueFromCurve/ColorFromCurve",
}
RENDERERS = {
    "sprite": "NiagaraSpriteRendererProperties",
    "ribbon": "NiagaraRibbonRendererProperties",
    "mesh": "NiagaraMeshRendererProperties",
    "light": "NiagaraLightRendererProperties",
}


class VfxError(RuntimeError):
    pass


# --------------------------------------------------------------------------- plumbing

def lib():
    """The C++ library (unreal.UETKVfxLibrary)."""
    cls = getattr(unreal, "UETKVfxLibrary", None)
    if cls is None:
        raise VfxError("the UETKVfx plugin is not loaded: run `ue.py vfx install` in the project, "
                       "then `ue.py build --restart`")
    return cls


def _call(name, *args):
    raw = getattr(lib(), name)(*args)
    result = json.loads(raw)
    if not result.pop("ok", False):
        raise VfxError(result.get("error", raw))
    return result


def system(target):
    """A NiagaraSystem from a path or object."""
    if isinstance(target, unreal.NiagaraSystem):
        return target
    obj = EAL.load_asset(target) if EAL.does_asset_exist(target.split(".")[0]) else None
    if not isinstance(obj, unreal.NiagaraSystem):
        raise VfxError("no Niagara system at %s" % target)
    return obj


def _asset(path, what="asset"):
    path = MODULES.get(path, path)
    obj = unreal.load_asset(path)
    if obj is None:
        raise VfxError("%s not found: %s" % (what, path))
    return obj


def _text(value):
    """Python value -> the text SetInput/SetUserParameter parse."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(float(value)) if isinstance(value, float) else str(value)
    if isinstance(value, (list, tuple)):
        return ", ".join(_text(v) for v in value)
    if isinstance(value, (unreal.Vector, unreal.Vector2D)):
        return _text(value.to_tuple())
    if isinstance(value, unreal.LinearColor):
        return _text((value.r, value.g, value.b, value.a))
    return str(value)


# --------------------------------------------------------------------------- systems & emitters

def new_system(path, overwrite=False):
    """Create an empty Niagara system (System State only). overwrite=True empties an existing one in place
    (deleting and recreating a loaded asset would make the editor ask "Overwrite Existing Object"),
    so actors and Blueprints that reference it keep working."""
    path = path.split(".")[0]
    if EAL.does_asset_exist(path):
        if not overwrite:
            raise VfxError("%s exists (pass overwrite=True)" % path)
        existing = system(path)
        _call("clear_system", existing)
        return existing
    folder, name = path.rsplit("/", 1)
    obj = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        name, folder, unreal.NiagaraSystem, unreal.NiagaraSystemFactoryNew())
    if obj is None:
        raise VfxError("could not create %s" % path)
    return obj


def add_emitter(sys_, name, template="Minimal", keep_parent=False):
    """Add a copy of a template emitter (name in /Niagara/DefaultAssets/Templates/Emitters or an asset path)."""
    source = template if template.startswith("/") else TEMPLATES + template
    return _call("add_emitter", system(sys_), _asset(source, "emitter"), name, keep_parent)["emitter"]


def remove_emitter(sys_, emitter):
    return _call("remove_emitter", system(sys_), emitter)


def emitters(sys_):
    """[{name, enabled, sim, renderers: [{class, enabled, materials: [{path, blend}]}]}] (cheap; no stack)."""
    return _call("list_emitters", system(sys_))["emitters"]


def emitter_property(sys_, emitter, prop, value):
    """Set an FVersionedNiagaraEmitterData field from text: ('SimTarget', 'GPUComputeSim'), ('bLocalSpace', 'true')."""
    return _call("set_emitter_property", system(sys_), emitter, prop, _text(value))["value"]


def set_property(obj, prop, value):
    """Set any edit-visible property (dotted paths walk into structs) from T3D text or a Python value."""
    return _call("set_object_property", obj, prop, value if isinstance(value, str) else _text(value))["value"]


def user_parameter(sys_, name, type_, value=None):
    """Add/update a user parameter (float, int, bool, vec2, vec3, vec4, color, position)."""
    return _call("set_user_parameter", system(sys_), name, type_, "" if value is None else _text(value))["parameter"]


# --------------------------------------------------------------------------- modules

def modules_in(sys_, emitter, stage):
    """Module names of a stage, in order."""
    return _call("list_modules", system(sys_), emitter, stage)["modules"]


def add_module(sys_, emitter, stage, module, index=-1, before=None, after=None):
    """Add a module script (short name from vfx.MODULES or a path). before/after: a module name in that stage.
    Returns the name to address it by ('Drag', or 'Drag#2' for a second one)."""
    sys_ = system(sys_)
    if before or after:
        names = [n.lower().replace(" ", "") for n in modules_in(sys_, emitter, stage)]
        key = (before or after).lower().replace(" ", "")
        if key in names:
            index = names.index(key) + (1 if after else 0)
    return _call("add_module", sys_, emitter, stage, _asset(module, "module"), index)["module"]


def ensure_module(sys_, emitter, stage, module, name=None, before=None, after=None):
    """The module's name in the stage, adding it if no module of that name is there yet."""
    script = _asset(module, "module")
    wanted = (name or script.get_name()).lower().replace(" ", "").replace("_", "")
    for existing in modules_in(sys_, emitter, stage):
        if existing.lower().replace(" ", "").replace("_", "") == wanted:
            return existing
    return add_module(sys_, emitter, stage, module, before=before, after=after)


def remove_module(sys_, emitter, stage, module):
    return _call("remove_module", system(sys_), emitter, stage, module)


def enable_module(sys_, emitter, stage, module, enabled=True):
    return _call("set_module_enabled", system(sys_), emitter, stage, module, enabled)


# --------------------------------------------------------------------------- inputs

def input(sys_, emitter, stage, module, path):
    """One input as a dict: path, type, mode, value, choices, nested inputs."""
    return _call("get_input", system(sys_), emitter, stage, module, path)


def set_input(sys_, emitter, stage, module, path, value):
    """Set a module input; see the module docstring for the value forms. Returns what was set."""
    sys_ = system(sys_)
    args = (sys_, emitter, stage, module, path)
    if not isinstance(value, dict):
        return _call("set_input", *args, _text(value))
    if value.get("reset"):
        return _call("reset_input", *args)
    if "link" in value:
        return _call("set_input_linked", *args, value["link"])
    if "expression" in value:
        return _call("set_input_expression", *args, value["expression"])
    if "object" in value:
        return _call("set_input_object", *args, _asset(value["object"], "object"))
    if "dynamic" in value:
        result = _call("set_input_dynamic", *args, _asset(value["dynamic"], "dynamic input"))
        for child, child_value in (value.get("inputs") or {}).items():
            set_input(sys_, emitter, stage, module, path + "/" + child, child_value)
        return result
    if "random" in value:
        lo, hi = value["random"]
        type_ = input(*args)["type"]
        if type_ not in _RANDOM:
            raise VfxError("no random-range dynamic input for %s (%s)" % (path, type_))
        _call("set_input_dynamic", *args, _asset(_RANDOM[type_], "dynamic input"))
        set_input(sys_, emitter, stage, module, path + "/Minimum", lo)
        set_input(sys_, emitter, stage, module, path + "/Maximum", hi)
        return {"input": path, "random": [lo, hi]}
    if "curve" in value:
        return _set_curve(args, value)
    raise VfxError("unknown value form for %s: %r" % (path, value))


def _set_curve(args, value):
    keys, interp = value["curve"], value.get("interp", "cubic")
    sys_, emitter, stage, module, path = args
    info = input(*args)
    type_ = info["type"]
    if "Curve" in type_ and "DataInterface" in type_:
        return _call("set_input_curve", *args, json.dumps(keys), interp)
    if type_ not in _CURVE:
        raise VfxError("no curve dynamic input for %s (%s)" % (path, type_))
    children = _call("set_input_dynamic", *args, _asset(_CURVE[type_], "dynamic input"))["inputs"]
    curve_child = next((c.split(" (")[0] for c in children if "Curve (Niagara" in c or "DataInterface" in c), None)
    if not curve_child:
        raise VfxError("the curve dynamic input on %s has no curve child: %s" % (path, children))
    result = _call("set_input_curve", sys_, emitter, stage, module, curve_child, json.dumps(keys), interp)
    if "index" in value:
        set_input(sys_, emitter, stage, module, curve_child.rsplit("/", 1)[0] + "/CurveIndex", {"link": value["index"]})
    return result


# --------------------------------------------------------------------------- renderers

def add_renderer(sys_, emitter, kind, **props):
    """Add a renderer ('sprite', 'ribbon', 'mesh', 'light' or a class name) and set raw properties on it.
    Returns the renderer object (set more with vfx.set_property(renderer, 'Prop', 'text'))."""
    sys_ = system(sys_)
    # By script path: not every renderer class is exported to Python as unreal.<Name>.
    cls = unreal.load_class(None, "/Script/Niagara." + RENDERERS.get(kind, kind))
    if cls is None:
        raise VfxError("unknown renderer %r (sprite, ribbon, mesh, light, or a Niagara renderer class name)" % kind)
    index = _call("add_renderer", sys_, emitter, cls)["index"]
    renderer = lib().get_renderers(sys_, emitter)[index]
    for key, val in props.items():
        set_property(renderer, key, val)
    return renderer


def renderers(sys_, emitter):
    return list(lib().get_renderers(system(sys_), emitter))


def remove_renderers(sys_, emitter, index=-1):
    return _call("remove_renderer", system(sys_), emitter, index)


def _object_ref(path):
    """T3D reference text for an asset path, e.g. for a renderer's Material property."""
    obj = unreal.load_asset(path)
    if obj is None:
        raise VfxError("asset not found: %s" % path)
    return "%s'%s'" % (obj.get_class().get_path_name(), obj.get_path_name())


_ALIGNMENT = {"unaligned": "Unaligned", "velocity": "VelocityAligned", "custom": "CustomAlignment", "auto": "Automatic"}
_FACING = {"camera": "FaceCamera", "plane": "FaceCameraPlane", "custom": "CustomFacingVector",
           "position": "FaceCameraPosition", "blend": "FaceCameraDistanceBlend", "auto": "Automatic"}
_SORT = {"none": "None", "view_depth": "ViewDepth", "view_distance": "ViewDistance",
         "custom_ascending": "CustomAscending", "custom_descending": "CustomDecending"}


def _apply_renderer(sys_, emitter, spec, material_folder):
    spec = dict(spec)
    kind = spec.pop("type", "sprite")
    props = dict(spec.pop("props", {}) or {})
    renderer = add_renderer(sys_, emitter, kind)
    material = spec.pop("material", {"sprite": "glow", "ribbon": "ribbon", "mesh": None, "light": None}.get(kind))
    if kind == "sprite":
        if material:
            set_property(renderer, "Material", _object_ref(vfx_assets.material(material, material_folder)))
        if "alignment" in spec:
            set_property(renderer, "Alignment", _ALIGNMENT.get(spec.pop("alignment"), "Unaligned"))
        if "facing" in spec:
            set_property(renderer, "FacingMode", _FACING.get(spec.pop("facing"), "FaceCamera"))
        if "subimages" in spec:
            x, y = spec.pop("subimages")
            set_property(renderer, "SubImageSize", "(X=%s,Y=%s)" % (float(x), float(y)))
        if "sort" in spec:
            set_property(renderer, "SortMode", _SORT.get(spec.pop("sort"), "None"))
    elif kind == "ribbon":
        if material:
            set_property(renderer, "Material", _object_ref(vfx_assets.material(material, material_folder)))
    elif kind == "mesh":
        mesh = spec.pop("mesh", "/Engine/BasicShapes/Cube")
        set_property(renderer, "Meshes", "((Mesh=%s))" % _object_ref(mesh))
        if material:
            set_property(renderer, "bOverrideMaterials", "true")
            set_property(renderer, "OverrideMaterials", "((ExplicitMat=%s))"
                         % _object_ref(vfx_assets.material(material, material_folder)))
    elif kind == "light":
        # One light per particle: colour = particle colour (HDR), radius = Particles.LightRadius (100 unless a
        # module writes it) x RadiusScale.
        if "radius" in spec:
            set_property(renderer, "RadiusScale", float(spec.pop("radius")) / 100.0)
        if "radius_scale" in spec:
            set_property(renderer, "RadiusScale", float(spec.pop("radius_scale")))
        if "intensity" in spec:
            intensity = float(spec.pop("intensity"))
            set_property(renderer, "DiffuseScale", intensity)
            set_property(renderer, "SpecularScale", intensity)
        if "inverse_squared" in spec:
            set_property(renderer, "bUseInverseSquaredFalloff", bool(spec.pop("inverse_squared")))
    for key, val in props.items():
        try:
            set_property(renderer, key, val)
        except VfxError as exc:
            unreal.log_warning("vfx: renderer %s.%s: %s" % (kind, key, exc))
    if spec:
        unreal.log_warning("vfx: unused renderer keys %s" % sorted(spec))
    return renderer


# --------------------------------------------------------------------------- compile, save, preview, describe

def compile(sys_, force=True, raise_on_error=True):
    """Compile and wait. Returns {'ready', 'errors', 'warnings', 'scripts'}; raises on errors by default."""
    result = _call("compile", system(sys_), force)
    if raise_on_error and result["errors"]:
        raise VfxError("compile errors:\n  " + "\n  ".join(result["errors"]))
    return result


def save(sys_):
    obj = system(sys_)
    return unreal.EditorAssetLibrary.save_asset(obj.get_path_name().split(".")[0], only_if_is_dirty=False)


def release(sys_=None):
    """Drop the cached editing view model (build() does this at the end)."""
    lib().release_editing(system(sys_) if sys_ else None)


def _default_preview_path(sys_):
    name = system(sys_).get_name()
    return os.path.join(unreal.Paths.project_saved_dir(), "UnrealToolkit", "vfx", name + ".png")


def preview(sys_, times=(0.1, 0.4, 0.8, 1.5), out=None, size=320, columns=0, yaw=0.0, pitch=-15.0,
            distance=0.0, fov=50.0, zoom=1.0, background=None, environment=False, floor=False, exposure=0.0,
            target=None, labels=True):
    """Render the system offscreen at each age into one PNG contact sheet (same camera for every frame).
    Returns {'path', 'frames': [{time, particles, emitters}], 'bounds_min/max', 'distance', 'background'}:
    n=0 everywhere means nothing spawned (GPU emitters' counts arrive a frame or more late).
    The camera fits the bounds over all times; zoom=2 halves the distance when late debris makes that loose.
    background: None = auto (mid-gray when an emitter uses a non-additive material, else black), 'black',
    or an (r, g, b) linear colour. target=(x, y, z) with distance>0 frames a fixed spot."""
    settings = unreal.UETKVfxPreviewSettings()
    settings.set_editor_property("zoom", float(zoom))
    if background != "black":
        settings.set_editor_property("backdrop_material", unreal.load_asset(vfx_assets.material("backdrop")))
    if background is None:
        settings.set_editor_property("background_color", unreal.LinearColor(0, 0, 0, -1))
    elif background == "black":
        settings.set_editor_property("background_color", unreal.LinearColor(0, 0, 0, 1))
    else:
        r, g, b = list(background)[:3]
        settings.set_editor_property("background_color", unreal.LinearColor(r, g, b, 1))
    settings.set_editor_property("times", [float(t) for t in times])
    settings.set_editor_property("frame_size", int(size))
    settings.set_editor_property("columns", int(columns))
    settings.set_editor_property("camera_yaw", float(yaw))
    settings.set_editor_property("camera_pitch", float(pitch))
    settings.set_editor_property("camera_distance", float(distance))
    settings.set_editor_property("fov", float(fov))
    settings.set_editor_property("show_environment", bool(environment))
    settings.set_editor_property("show_floor", bool(floor))
    settings.set_editor_property("exposure_bias", float(exposure))
    settings.set_editor_property("labels", bool(labels))
    if target is not None:
        settings.set_editor_property("auto_frame", False)
        settings.set_editor_property("target", unreal.Vector(*target))
    path = out or _default_preview_path(sys_)
    return _call("render_preview", system(sys_), path, settings)


def describe(sys_, text=True, hidden=False, inputs=True):
    """The system's stack: emitters, modules, renderers, issues. Text shows only inputs changed from the module
    defaults (and their parents); hidden=True shows every input, set or not. text=False returns the full dict
    (each input has 'changed' when it differs from its default)."""
    data = _call("describe", system(sys_), inputs, hidden)
    release(sys_)
    return _describe_text(data, hidden) if text else data


def _changed(inp):
    return inp.get("changed") or any(_changed(c) for c in inp.get("inputs", []))


def _input_lines(inp, indent, hidden):
    mode = inp.get("mode")
    if not hidden and (mode in ("none", "default_function") or not _changed(inp)):
        return []
    flags = "".join(" (%s)" % f for f in ("static", "hidden", "disabled") if inp.get(f))
    value = inp.get("value", "")
    head = "%s%s = %s" % (indent, inp["name"], value if mode == "local" else "<%s> %s" % (mode, value))
    lines = [head.rstrip() + "  : " + inp["type"] + flags + ("" if inp.get("changed") or not hidden else "  (default)")]
    for child in inp.get("inputs", []):
        lines += _input_lines(child, indent + "    ", hidden)
    return lines


def _describe_text(data, hidden):
    lines = ["system %s" % data["system"]]
    for p in data.get("user_parameters", []):
        lines.append("  user %s : %s = %s" % (p["name"], p["type"], p.get("value", "")))
    for stage, mods in (data.get("system_stages") or {}).items():
        lines.append("  [%s]" % stage)
        for m in mods:
            lines.append("    %s%s" % (m["name"], "" if m.get("enabled", True) else "  (disabled)"))
            for inp in m.get("inputs", []):
                lines += _input_lines(inp, "        ", hidden)
    for e in data.get("emitters", []):
        lines.append("emitter %s%s  %s" % (e["name"], "" if e.get("enabled", True) else " (disabled)",
                                           ", ".join("%s=%s" % kv for kv in sorted(e.get("properties", {}).items())
                                                     if kv[0] not in ("Platforms",))))
        for stage, mods in e.get("stages", {}).items():
            lines.append("  [%s]" % stage)
            for m in mods:
                lines.append("    %s%s" % (m["name"], "" if m.get("enabled", True) else "  (disabled)"))
                for inp in m.get("inputs", []):
                    lines += _input_lines(inp, "        ", hidden)
        for r in e.get("renderers", []):
            props = ", ".join("%s=%s" % kv for kv in sorted(r["properties"].items())
                              if not kv[0].endswith("Binding") and kv[0] not in ("Platforms",))
            lines.append("  renderer %d %s  %s" % (r["index"], r["class"], props))
        for issue in e.get("issues", []):
            if issue["summary"] != "Object has warnings":
                lines.append("  %s: %s %s" % (issue["severity"], issue["where"], issue["summary"]))
    return "\n".join(lines)


def place(sys_, location=(0, 0, 0), rotation=(0, 0, 0), label=None):
    """Spawn a NiagaraActor playing the system in the open level (marks the level dirty; not saved)."""
    sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actor = sub.spawn_actor_from_class(unreal.NiagaraActor, unreal.Vector(*location),
                                       unreal.Rotator(pitch=rotation[0], yaw=rotation[1], roll=rotation[2]))
    actor.get_editor_property("niagara_component").set_asset(system(sys_))
    if label:
        actor.set_actor_label(label)
    return actor.get_actor_label()


# --------------------------------------------------------------------------- the spec builder

def _range(value):
    return isinstance(value, (list, tuple)) and len(value) == 2 and all(isinstance(v, (int, float)) for v in value)


class _Emitter:
    """Applies one emitter spec; remembers modules it added so forces land before the solver."""

    def __init__(self, sys_, spec, material_folder):
        self.sys = sys_
        self.spec = spec
        self.name = spec["name"]
        self.material_folder = material_folder

    def set(self, stage, module, path, value):
        return set_input(self.sys, self.name, stage, module, path, value)

    def module(self, stage, key, before=None, name=None):
        return ensure_module(self.sys, self.name, stage, key, name=name, before=before)

    def force(self, key):
        """A particle-update force module, placed before the solver (added at the end if missing)."""
        self.module("particle_update", "solver", name=SOLVER)
        return self.module("particle_update", key, before=SOLVER)

    def build(self):
        s, spec = self.sys, self.spec
        add_emitter(s, self.name, spec.get("template", "Minimal"), spec.get("keep_parent", False))
        self.properties()
        self.cycle()
        self.spawn()
        self.initialize()
        self.shape()
        self.velocity()
        self.forces()
        self.over_life()
        self.extras()
        if any(k in spec for k in ("renderer", "renderers")):
            remove_renderers(s, self.name)
            for r in ([spec["renderer"]] if "renderer" in spec else []) + list(spec.get("renderers", [])):
                _apply_renderer(s, self.name, r, self.material_folder)

    # -- emitter-level
    def properties(self):
        spec = self.spec
        if "sim" in spec:
            emitter_property(self.sys, self.name, "SimTarget",
                             "GPUComputeSim" if spec["sim"].lower().startswith("g") else "CPUSim")
        if "local_space" in spec:
            emitter_property(self.sys, self.name, "bLocalSpace", bool(spec["local_space"]))
        if "bounds" in spec:
            (x0, y0, z0), (x1, y1, z1) = spec["bounds"]
            emitter_property(self.sys, self.name, "CalculateBoundsMode", "Fixed")
            emitter_property(self.sys, self.name, "FixedBounds",
                             "(Min=(X=%s,Y=%s,Z=%s),Max=(X=%s,Y=%s,Z=%s),IsValid=True)" % (x0, y0, z0, x1, y1, z1))
        elif spec.get("sim", "cpu").lower().startswith("g"):
            emitter_property(self.sys, self.name, "CalculateBoundsMode", "Fixed")
            emitter_property(self.sys, self.name, "FixedBounds",
                             "(Min=(X=-1000,Y=-1000,Z=-1000),Max=(X=1000,Y=1000,Z=1000),IsValid=True)")
        for key, val in (spec.get("properties") or {}).items():
            emitter_property(self.sys, self.name, key, val)

    def cycle(self):
        spec = self.spec
        if not any(k in spec for k in ("loop", "duration", "loop_count", "delay")):
            return
        m = self.module("emitter_update", "emitter_state", name="Emitter State")
        self.set("emitter_update", m, "Life Cycle Mode", "Self")
        _apply_loop(lambda path, v: self.set("emitter_update", m, path, v), spec)

    def spawn(self):
        spawn = self.spec.get("spawn")
        if not spawn:
            return
        if "rate" in spawn:
            m = self.module("emitter_update", "spawn_rate", name="Spawn Rate")
            self.set("emitter_update", m, "SpawnRate", spawn["rate"])
        bursts = list(spawn.get("bursts", []))
        if "burst" in spawn:
            bursts.insert(0, [spawn["burst"], spawn.get("time", 0)])
        for count, when in bursts:
            m = add_module(self.sys, self.name, "emitter_update", "spawn_burst")
            self.set("emitter_update", m, "Spawn Count", count)
            if when:
                self.set("emitter_update", m, "Spawn Time", when)
        if "per_unit" in spawn:
            m = self.module("emitter_update", "spawn_per_unit", name="Spawn Per Unit")
            self.set("emitter_update", m, "Spawn Spacing", spawn["per_unit"])

    def initialize(self):
        spec = self.spec
        keys = ("lifetime", "color", "size", "mass", "rotation", "mesh_scale", "ribbon_width")
        if not any(k in spec for k in keys):
            return
        st = "particle_spawn"
        m = self.module(st, "initialize_particle", name="Initialize Particle")
        if "lifetime" in spec:
            v = spec["lifetime"]
            if _range(v):
                self.set(st, m, "Lifetime Mode", "Random")
                self.set(st, m, "Lifetime Min", v[0])
                self.set(st, m, "Lifetime Max", v[1])
            else:
                self.set(st, m, "Lifetime Mode", "Direct Set")
                self.set(st, m, "Lifetime Mode/Lifetime", v)
        if "color" in spec:
            v = spec["color"]
            if isinstance(v, dict) and "random" in v:
                self.set(st, m, "Color Mode", "Random Range")
                self.set(st, m, "Color Minimum", _rgba(v["random"][0]))
                self.set(st, m, "Color Maximum", _rgba(v["random"][1]))
                channels = {"rgba": "Link RGBA", "rgb_a": "Link RGB / Link A",
                            "individual": "Random Individual Channels"}.get(v.get("channels", "rgb_a"))
                self.set(st, m, "Color Channel Mode", channels)
            else:
                self.set(st, m, "Color Mode", "Direct Set")
                self.set(st, m, "Color Mode/Color", v if isinstance(v, dict) else _rgba(v))
        if "size" in spec:
            v = spec["size"]
            if isinstance(v, dict) and "xy" in v:
                self.set(st, m, "Sprite Size Mode", "Non-Uniform")
                self.set(st, m, "Sprite Size", v["xy"])
            elif isinstance(v, dict) and "xy_random" in v:
                self.set(st, m, "Sprite Size Mode", "Random Non-Uniform")
                self.set(st, m, "Sprite Size Min", v["xy_random"][0])
                self.set(st, m, "Sprite Size Max", v["xy_random"][1])
            elif _range(v):
                self.set(st, m, "Sprite Size Mode", "Random Uniform")
                self.set(st, m, "Uniform Sprite Size Min", v[0])
                self.set(st, m, "Uniform Sprite Size Max", v[1])
            else:
                self.set(st, m, "Sprite Size Mode", "Uniform")
                self.set(st, m, "Uniform Sprite Size", v)
        if "mass" in spec:
            v = spec["mass"]
            if _range(v):
                self.set(st, m, "Mass Mode", "Random")
                self.set(st, m, "Mass Min", v[0])
                self.set(st, m, "Mass Max", v[1])
            else:
                self.set(st, m, "Mass Mode", "Direct Set")
                self.set(st, m, "Mass Mode/Mass", v)
        if "rotation" in spec:
            v = spec["rotation"]
            if v == "random":
                self.set(st, m, "Sprite Rotation Mode", "Random")
            else:
                self.set(st, m, "Sprite Rotation Mode", "Direct Angle (Degrees)")
                self.set(st, m, "Sprite Rotation Angle", v)
        if "mesh_scale" in spec:
            v = spec["mesh_scale"]
            if isinstance(v, dict) and "xyz" in v:
                self.set(st, m, "Mesh Scale Mode", "Non-Uniform")
                self.set(st, m, "Mesh Scale Mode/Mesh Scale", v["xyz"])
            elif _range(v):
                self.set(st, m, "Mesh Scale Mode", "Random Uniform")
                self.set(st, m, "Mesh Uniform Scale Min", v[0])
                self.set(st, m, "Mesh Uniform Scale Max", v[1])
            else:
                self.set(st, m, "Mesh Scale Mode", "Uniform")
                self.set(st, m, "Mesh Uniform Scale", v)
        if "ribbon_width" in spec:
            self.set(st, m, "Ribbon Width Mode", "Direct Set")
            self.set(st, m, "Ribbon Width", spec["ribbon_width"])

    def shape(self):
        shape = self.spec.get("shape")
        if not shape:
            return
        st = "particle_spawn"
        if shape.get("point"):
            return
        m = self.module(st, "shape_location", name="Shape Location")
        if "sphere" in shape:
            self.set(st, m, "Shape Primitive", "Sphere")
            self.set(st, m, "Sphere Radius", shape["sphere"])
        elif "box" in shape:
            self.set(st, m, "Shape Primitive", "Box / Plane")
            self.set(st, m, "Box / Plane Mode", "Box")
            self.set(st, m, "Box Size", shape["box"])
        elif "plane" in shape:
            self.set(st, m, "Shape Primitive", "Box / Plane")
            self.set(st, m, "Box / Plane Mode", "Plane")
            self.set(st, m, "Plane Size", shape["plane"])
        elif "cylinder" in shape:
            radius, height = shape["cylinder"]
            self.set(st, m, "Shape Primitive", "Cylinder")
            self.set(st, m, "Cylinder Radius", radius)
            self.set(st, m, "Cylinder Height", height)
        elif "ring" in shape:
            self.set(st, m, "Shape Primitive", "Ring / Disc")
            self.set(st, m, "Ring Radius", shape["ring"])
            if "coverage" in shape:
                self.set(st, m, "Disc Coverage", shape["coverage"])
        elif "cone" in shape:
            length, angle = shape["cone"]
            self.set(st, m, "Shape Primitive", "Cone")
            self.set(st, m, "Cone Length", length)
            self.set(st, m, "Cone Angle", angle)
        elif "torus" in shape:
            radius, tube = shape["torus"]
            self.set(st, m, "Shape Primitive", "Torus")
            self.set(st, m, "Large Radius", radius)
            self.set(st, m, "Handle Radius", tube)
        for key, val in (shape.get("inputs") or {}).items():
            self.set(st, m, key, val)

    def velocity(self):
        v = self.spec.get("velocity")
        if v is None:
            return
        st = "particle_spawn"
        self.module("particle_update", "solver", name=SOLVER)
        m = self.module(st, "add_velocity", name="Add Velocity")
        if isinstance(v, dict) and "cone" in v:
            self.set(st, m, "Velocity Mode", "In Cone")
            self.set(st, m, "Cone Axis", v.get("axis", [0, 0, 1]))
            self.set(st, m, "Cone Angle", v["cone"])
            self.set(st, m, "Velocity Speed", _value(v.get("speed", 100)))
        elif isinstance(v, dict) and "point" in v:
            self.set(st, m, "Velocity Mode", "From Point")
            self.set(st, m, "Velocity Speed", _value(v["point"]))
        else:
            self.set(st, m, "Velocity Mode", "Linear")
            self.set(st, m, "Velocity", v)

    def forces(self):
        spec, pu = self.spec, "particle_update"
        if "gravity" in spec:
            g = spec["gravity"]
            self.set(pu, self.force("gravity"), "Gravity", [0, 0, g] if isinstance(g, (int, float)) else g)
        if "acceleration" in spec:
            self.set(pu, self.force("acceleration"), "Acceleration", spec["acceleration"])
        if "wind" in spec:
            m = self.force("wind")
            self.set(pu, m, "Wind Speed", spec["wind"])
        if "curl_noise" in spec:
            c = spec["curl_noise"]
            c = c if isinstance(c, dict) else {"strength": c}
            m = self.force("curl_noise")
            self.set(pu, m, "Noise Strength", c.get("strength", 50))
            if "frequency" in c:
                self.set(pu, m, "Noise Frequency", c["frequency"])
        if "vortex" in spec:
            v = spec["vortex"]
            v = v if isinstance(v, dict) else {"amount": v}
            m = self.force("vortex")
            self.set(pu, m, "Vortex Force Amount", v.get("amount", 200))
            if "axis" in v:
                self.set(pu, m, "Vortex Axis", v["axis"])
            if "pull" in v:
                self.set(pu, m, "Origin Pull Amount", v["pull"])
        if "attract" in spec:
            a = spec["attract"]
            m = self.force("point_attraction")
            self.set(pu, m, "AttractionStrength", a.get("strength", 100))
            self.set(pu, m, "Attraction Radius", a.get("radius", 200))
        if "drag" in spec:
            self.set(pu, self.force("drag"), "Drag", spec["drag"])

    def over_life(self):
        spec, pu = self.spec, "particle_update"
        if "color_over_life" in spec or "alpha_over_life" in spec:
            m = self.module(pu, "scale_color", name="Scale Color")
            self.set(pu, m, "Scale Mode", "0")  # RGB and alpha scaled separately
            if "color_over_life" in spec:
                keys = spec["color_over_life"]
                self.set(pu, m, "Scale RGB", {"curve": [[k[0], k[1], k[2], k[3]] for k in keys]})
                self.set(pu, m, "Scale Alpha", {"curve": [[k[0], k[4] if len(k) > 4 else 1] for k in keys]})
            if "alpha_over_life" in spec:
                self.set(pu, m, "Scale Alpha", {"curve": spec["alpha_over_life"]})
        if "size_over_life" in spec:
            if any(r.get("type") == "mesh" for r in _renderer_specs(spec)):
                m = self.module(pu, "scale_mesh_size", name="Scale Mesh Size")
                keys = [[k[0], k[1], k[1], k[1]] for k in spec["size_over_life"]]
                self.set(pu, m, "Scale Factor", {"curve": keys})
            else:
                m = self.module(pu, "scale_sprite_size", name="Scale Sprite Size")
                self.set(pu, m, "Scale Sprite Size Mode", "Uniform Curve")
                self.set(pu, m, "Uniform Curve Sprite Scale", {"curve": spec["size_over_life"]})
        if "rotation_rate" in spec:
            m = self.module(pu, "sprite_rotation_rate", name="Sprite Rotation Rate")
            self.set(pu, m, "Rotation Rate", _value(spec["rotation_rate"]))
        if "subuv" in spec:
            m = self.module(pu, "subuv", name="Sub UVAnimation")
            mode = spec["subuv"]
            if mode == "random":
                self.set(pu, m, "SubUV Animation Mode", "Random")
                self.set(pu, m, "Evaluation Type", "Spawn Only")
            elif mode == "loop":
                self.set(pu, m, "SubUV Animation Mode", "Infinite Loop")
            else:
                self.set(pu, m, "SubUV Animation Mode", "Linear")

    def extras(self):
        spec = self.spec
        if spec.get("collision"):
            c = spec["collision"] if isinstance(spec["collision"], dict) else {}
            m = self.force("collision")
            if "restitution" in c:
                self.set("particle_update", m, "Restitution", c["restitution"])
            if "friction" in c:
                self.set("particle_update", m, "Friction", c["friction"])
        for item in spec.get("modules", []):
            m = add_module(self.sys, self.name, item["stage"], item["module"], index=item.get("index", -1),
                           before=item.get("before"), after=item.get("after"))
            for path, val in (item.get("inputs") or {}).items():
                self.set(item["stage"], m, path, val)
        for item in spec.get("inputs", []):
            self.set(item["stage"], item["module"], item["input"], item["value"])


def _renderer_specs(spec):
    return ([spec["renderer"]] if "renderer" in spec else []) + list(spec.get("renderers", []))


def _rgba(v):
    v = list(v)
    return v + [1.0] if len(v) == 3 else v


def _value(v):
    """[a, b] -> a random range; anything else unchanged."""
    return {"random": list(v)} if _range(v) else v


def _apply_loop(setter, spec):
    loop = spec.get("loop")
    if loop:
        setter("Loop Behavior", {"once": "Once", "infinite": "Infinite", "multiple": "Multiple"}[loop])
    if "duration" in spec:
        setter("Loop Duration", spec["duration"])
    if "loop_count" in spec:
        setter("Loop Count", spec["loop_count"])
    if "delay" in spec:
        setter("Loop Delay", spec["delay"])


def build(spec, path=None, overwrite=True, save_asset=True, material_folder=vfx_assets.DEFAULT_FOLDER):
    """Build a Niagara system from a spec dict (see the module docstring); compile and save it.
    Returns {'path', 'compile': {...}}. On an error the half-built asset is left for inspection."""
    path = path or spec.get("path")
    if not path:
        raise VfxError("build needs a path")
    s = new_system(path, overwrite=overwrite)
    try:
        if any(k in spec for k in ("loop", "duration", "loop_count")):
            _apply_loop(lambda p, v: set_input(s, "", "system_update", "System State", p, v), spec)
        for name, (type_, value) in (spec.get("user") or {}).items():
            user_parameter(s, name, type_, value)
        for em in spec.get("emitters", []):
            _Emitter(s, em, material_folder).build()
        if spec.get("warmup"):
            set_property(s, "WarmupTime", float(spec["warmup"]))
    finally:
        release(s)
    result = compile(s, force=True, raise_on_error=False)
    if save_asset:
        save(s)
    if result["errors"]:
        raise VfxError("built %s but it has compile errors:\n  %s" % (path, "\n  ".join(result["errors"])))
    return {"path": s.get_path_name().split(".")[0], "compile": {k: result[k] for k in ("ready", "warnings")}}


# --------------------------------------------------------------------------- presets

def presets():
    """{name: description + parameters} of the built-in presets (vfx_presets.py)."""
    import vfx_presets
    return vfx_presets.catalog()


def create(path, preset, preview_after=False, preview_options=None, **params):
    """Build a preset (see presets()) at path, e.g. create('/Game/VFX/NS_Fire', 'fire', color=(1, .3, .1)).
    preview_after=True also renders a contact sheet (at the preset's own times unless preview_options gives
    'times'; other preview() arguments go in preview_options too) and returns it under 'preview'."""
    import importlib
    import vfx_presets
    importlib.reload(vfx_presets)
    spec = vfx_presets.make(preset, **params)
    result = build(spec, path)
    if preview_after:
        options = dict(spec.get("preview") or {})
        options.update(preview_options or {})
        result["preview"] = preview(path, **options)
    return result


def module_catalog(filter=None, root="/Niagara/Modules"):
    """Module scripts under a content root (default Niagara's), optionally filtered by a name substring."""
    reg = unreal.AssetRegistryHelpers.get_asset_registry()
    out = []
    for data in reg.get_assets_by_path(root, recursive=True):
        if str(data.asset_class_path.asset_name) != "NiagaraScript":
            continue
        name = str(data.asset_name)
        if filter and filter.lower() not in str(data.package_name).lower():
            continue
        out.append(str(data.package_name))
    return sorted(out)
