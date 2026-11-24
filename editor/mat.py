"""mat - build and read materials from Python. Runs inside UnrealEditor; pre-imported as `mat` in `ue.py py`.

    mat.build(SPEC, '/Game/Materials/M_Rock')            # create, or rebuild in place; compile; save
    mat.describe('/Game/Materials/M_Rock')               # the graph as spec text (build() takes it back)
    mat.instance('/Game/Materials/MI_Rock_Red', '/Game/Materials/M_Rock', {'Tint': [1, .2, .2]})
    mat.function(SPEC, '/Game/Materials/MF_Tiling')      # a material function ('inputs' + 'out')
    mat.params('/Game/Materials/MI_Rock_Red')            # every parameter: type, value, overridden
    mat.preview('/Game/Materials/M_Rock', mesh='plane')  # PNG, offscreen (ueh.render_asset)
    mat.nodes('noise')                                   # material expression classes with their pins

Building uses the engine's MaterialEditingLibrary. Reading back (describe), compile errors, and the outputs Python
can't connect (pixel_depth_offset, shading_model, clear_coat...) need the UETKEditor plugin: `ue.py plugins install`.
Before UE 5.8 building needs it too (Python can't list a material's nodes or a node's outputs there). Compile errors
need a rendering editor: headless runs compile no shaders, and report errors=None.

SPEC (every key optional):
    {'domain': 'surface',          # surface | post_process | deferred_decal | light_function | volume | ui
     'blend': 'opaque',            # opaque | masked | translucent | additive | modulate | alpha_composite | alpha_holdout
     'shading': 'default_lit',     # default_lit | unlit | subsurface | clear_coat | two_sided_foliage | cloth | ...
     'two_sided': False,
     'usage': ['skeletal_mesh', 'niagara_sprites'],             # usage flags to switch on (MATUSAGE_*)
     'properties': {'opacity_mask_clip_value': 0.4,             # any Material property (snake_case or C++ name)
                    'blendable_location': 'after_tonemapping'},
     'params': {                   # parameters (instances override them), used by name in expressions
        'Tint': [1, 0.5, 0.2, 1],  #   vector
        'Roughness': 0.5,          #   scalar
        'Albedo': '/Game/T_Rock',  #   texture: sample(Albedo, uv)
        'Detail': True,            #   static switch: switch(Detail, a, b)
        'Speed': {'value': 1, 'group': 'Motion', 'min': 0, 'max': 5, 'desc': 'pan speed', 'priority': 1}},
     'let': {'uv': 'uv0 * Tiling'},                             # named steps, used by later lets and outs
     'out': {'base_color': 'sample(Albedo, uv).rgb * Tint', 'roughness': 'Roughness'}}
    Rebuilding replaces every node, and resets domain, blend, shading and two_sided to the spec's (or the defaults).

OUTPUTS: base_color metallic specular roughness anisotropy emissive opacity opacity_mask normal tangent
    world_position_offset subsurface_color ambient_occlusion refraction pixel_depth_offset shading_model clear_coat
    clear_coat_roughness displacement customized_uv0..7 material_attributes front_material

EXPRESSIONS (Python syntax):
    0.5, (1, 0, 0) constants; (uv.r, 0.5) appends; + - * / % ** ; 1 - x is a OneMinus node
    x.rgb, x.a, x.xy           swizzles (the node's own R/G/B/A/RGB/XY outputs when it has them, else a mask)
    x.Name                     one output pin by name: scene_texture(...).Size, fn(...).Result
    names                      params, lets, function inputs, and uv (uv0..uv7), time, world_pos, object_pos,
                               actor_pos, camera_pos, camera_dir, vertex_color, vertex_normal, pixel_normal,
                               screen_uv, pixel_pos, pixel_depth, scene_depth, object_radius, view_size,
                               particle_color, two_sided_sign, pi
    param('Base Color')        a parameter whose name isn't a Python name; input('UV Scale') likewise in functions
    sample(tex, uv)            tex: a texture param, an asset path '/Game/T_X', or a texture object. The sampler
                               type follows the texture (sRGB, normal map, masks); sampler='linear_color' overrides
    lerp(a, b, t) clamp(x, lo, hi) smoothstep(lo, hi, x) step(y, x) min max pow fmod atan2 dot cross distance
    saturate abs frac floor ceil round sign trunc sqrt exp log2 length normalize one_minus ddx ddy
    sin(x) cos(x)              radians (period 0); sin(x, period=1) is the editor's default Sine node
    fresnel(exponent, base, normal)  panner(uv, speed=(0.1, 0))  desaturate(color, fraction)  noise(pos, scale=0.1)
    transform(v, 'tangent', 'world')  transform_position(p, 'local', 'world')  compare(a, b, greater, equal, less)
    switch(Param, on, off)     a static switch parameter
    scene_texture('post_process_input0')            .Color (default), .Size, .InvSize
    custom('return A * B;', out='float3', A=x, B=y)  a Custom HLSL node (inputs in keyword order)
    fn('/Game/MF_X', x, Scale=2).Result              a material function call (inputs in order or by name)
    AnyNode(in1, in2, Prop=value)  any MaterialExpression class by name (SkyAtmosphereLightIlluminance,
                               RotateAboutAxis, ...): positional args feed its inputs in order (None skips one),
                               keywords feed an input with that name or set a property
    A number going into a node's input becomes that node's own constant (Add's ConstB, Lerp's ConstAlpha), not a
    Constant node. Repeated sub-expressions share one node.

FUNCTION SPEC (mat.function, or build() with 'function': True):
    {'inputs': {'UV': 'vector2', 'Scale': {'type': 'scalar', 'default': 1, 'desc': '...'}},   # in pin order
     'let': {...}, 'out': {'Result': 'input UV * Scale ...'},
     'description': '...', 'expose': True, 'categories': ['Custom']}
    Input types: scalar vector2 vector3 vector4 texture2d texture_cube texture2d_array volume_texture static_bool
    bool material_attributes.
"""
import ast
import difflib
import json
import math
import re

import unreal
from uprop import (PropError, asset as _asset, color as _color, convert as _convert, enum_name as _enum_name,
                   enum_value as _enum_value, load as _load, norm as _norm, object_path as _object_path,
                   prop_name as _prop_name, set_prop as _set_prop, short_path as _short_path, snake as _snake,
                   struct as _struct)

MEL = unreal.MaterialEditingLibrary
EAL = unreal.EditorAssetLibrary
PREFIX = "MaterialExpression"


class MaterialError(PropError):
    pass


# --------------------------------------------------------------------------- plumbing

def _plugin():
    return getattr(unreal, "UETKMaterialLibrary", None)


def _call(name, *args):
    lib = _plugin()
    if lib is None:
        raise MaterialError("needs the UETKEditor plugin: `ue.py plugins install`, then `ue.py build --restart`")
    raw = getattr(lib, name)(*args)
    result = json.loads(raw)
    if not result.pop("ok", False):
        raise MaterialError(result.get("error", raw))
    return result


def _plugin_lib():
    lib = _plugin()
    if lib is None:
        raise MaterialError("before UE 5.8 this needs the UETKEditor plugin (Python's MaterialEditingLibrary lacks it): "
                            "`ue.py plugins install`, then `ue.py build --restart`")
    return lib


def _expressions(owner, function=False):
    """Every node of a material or function (MaterialEditingLibrary has this from 5.8, the plugin before)."""
    name = "get_material_function_expressions" if function else "get_material_expressions"
    if hasattr(MEL, name):
        return list(getattr(MEL, name)(owner))
    return list(_plugin_lib().list_expressions(owner))


def _class_name(expr):
    name = expr.get_class().get_name()
    return name[len(PREFIX):] if name.startswith(PREFIX) else name


def _expr_class(name):
    camel = name if name[:1].isupper() else "".join(p[:1].upper() + p[1:] for p in name.split("_"))
    cls = getattr(unreal, PREFIX + camel, None)
    if cls is None:
        known = [n[len(PREFIX):] for n in dir(unreal) if n.startswith(PREFIX)]
        close = difflib.get_close_matches(camel, known, 3)
        raise MaterialError("no material node %r%s (mat.nodes() lists them)"
                            % (name, "; did you mean " + ", ".join(close) if close else ""))
    return cls


def _pins(expr):
    return [str(n) for n in MEL.get_material_expression_input_names(expr)]


def _outs(expr):
    if hasattr(MEL, "get_material_expression_output_names"):
        return [str(n) for n in MEL.get_material_expression_output_names(expr)]
    return [str(n) for n in _plugin_lib().expression_output_names(expr)]


def _match(names, key):
    k = _norm(key)
    for n in names:
        if _norm(n) == k:
            return n
    return None


# Literal numbers fed to these inputs go into the node's own constant instead of a Constant node. Inputs not
# listed try const_<input> and <input>_default (Add.ConstA, Lerp.ConstAlpha, Clamp.MinDefault, SmoothStep.ConstMin).
_CONSTS = {
    "Power": {"Exp": "const_exponent"},
    "Fresnel": {"ExponentIn": "exponent", "BaseReflectFractionIn": "base_reflect_fraction"},
    "Panner": {"Speed": ("speed_x", "speed_y")},
    "Desaturation": {},
    "TextureCoordinate": {},
}


def _const_prop(expr, pin):
    cls = _class_name(expr)
    table = _CONSTS.get(cls)
    if table is not None and pin in table:
        return table[pin]
    if not pin or pin == "None":
        return None
    base = _snake(pin.replace(" ", ""))
    for candidate in ("const_" + base, base + "_default"):
        if _prop_name(expr, candidate):
            return candidate
    return None


# --------------------------------------------------------------------------- values while building

class _Ref:
    """An output of a node ('' = its first)."""
    __slots__ = ("expr", "out")

    def __init__(self, expr, out=""):
        self.expr, self.out = expr, out


class _Lit:
    """A number or a tuple of numbers, not yet a node."""
    __slots__ = ("v",)

    def __init__(self, v):
        self.v = v


class _Named:
    """A texture or switch parameter, a texture path: what it becomes depends on where it's used."""
    __slots__ = ("kind", "name")

    def __init__(self, kind, name):
        self.kind, self.name = kind, name


_BUILTINS = {
    "uv": ("TextureCoordinate", {}, ""),
    "time": ("Time", {}, ""),
    "world_pos": ("WorldPosition", {}, ""),
    "object_pos": ("ObjectPositionWS", {}, ""),
    "actor_pos": ("ActorPositionWS", {}, ""),
    "camera_pos": ("CameraPositionWS", {}, ""),
    "camera_dir": ("CameraVectorWS", {}, ""),
    "vertex_color": ("VertexColor", {}, ""),
    "vertex_normal": ("VertexNormalWS", {}, ""),
    "pixel_normal": ("PixelNormalWS", {}, ""),
    "screen_uv": ("ScreenPosition", {}, "ViewportUV"),
    "pixel_pos": ("ScreenPosition", {}, "PixelPosition"),
    "pixel_depth": ("PixelDepth", {}, ""),
    "scene_depth": ("SceneDepth", {}, ""),
    "object_radius": ("ObjectRadius", {}, ""),
    "view_size": ("ViewSize", {}, ""),
    "particle_color": ("ParticleColor", {}, ""),
    "two_sided_sign": ("TwoSidedSign", {}, ""),
}
for _i in range(8):
    _BUILTINS["uv%d" % _i] = ("TextureCoordinate", {"coordinate_index": _i} if _i else {}, "")

# name -> (node class, property defaults)
_FUNCS = {
    "lerp": ("LinearInterpolate", {}), "clamp": ("Clamp", {}), "smoothstep": ("SmoothStep", {}),
    "step": ("Step", {}), "min": ("Min", {}), "max": ("Max", {}), "pow": ("Power", {}), "fmod": ("Fmod", {}),
    "atan2": ("Arctangent2", {}), "dot": ("DotProduct", {}), "cross": ("CrossProduct", {}),
    "distance": ("Distance", {}), "saturate": ("Saturate", {}), "abs": ("Abs", {}), "frac": ("Frac", {}),
    "floor": ("Floor", {}), "ceil": ("Ceil", {}), "round": ("Round", {}), "sign": ("Sign", {}),
    "trunc": ("Truncate", {}), "sqrt": ("SquareRoot", {}), "exp": ("Exponential", {}), "log2": ("Logarithm2", {}),
    "length": ("Length", {}), "normalize": ("Normalize", {}), "one_minus": ("OneMinus", {}), "ddx": ("DDX", {}),
    "ddy": ("DDY", {}), "sin": ("Sine", {"period": 0.0}), "cos": ("Cosine", {"period": 0.0}),
    "fresnel": ("Fresnel", {}), "panner": ("Panner", {}), "desaturate": ("Desaturation", {}),
    "noise": ("Noise", {}), "compare": ("If", {}),
}
_FUNC_BY_CLASS = {cls: name for name, (cls, _) in _FUNCS.items() if name not in ("one_minus",)}

_BINOPS = {ast.Add: "Add", ast.Sub: "Subtract", ast.Mult: "Multiply", ast.Div: "Divide", ast.Pow: "Power",
           ast.Mod: "Fmod"}
_CHANNELS = {"r": 0, "g": 1, "b": 2, "a": 3, "x": 0, "y": 1, "z": 2, "w": 3}
_SWIZZLE = re.compile(r"^[rgba]{1,4}$|^[xyzw]{1,4}$")

_OUTPUTS = {
    "base_color": "BaseColor", "metallic": "Metallic", "specular": "Specular", "roughness": "Roughness",
    "anisotropy": "Anisotropy", "emissive": "EmissiveColor", "emissive_color": "EmissiveColor",
    "opacity": "Opacity", "opacity_mask": "OpacityMask", "normal": "Normal", "tangent": "Tangent",
    "world_position_offset": "WorldPositionOffset", "wpo": "WorldPositionOffset",
    "subsurface_color": "SubsurfaceColor", "ambient_occlusion": "AmbientOcclusion", "ao": "AmbientOcclusion",
    "refraction": "Refraction", "pixel_depth_offset": "PixelDepthOffset", "shading_model": "ShadingModel",
    "clear_coat": "CustomData0", "clear_coat_roughness": "CustomData1", "displacement": "Displacement",
    "material_attributes": "MaterialAttributes", "front_material": "FrontMaterial",
    "surface_thickness": "SurfaceThickness",
}
for _i in range(8):
    _OUTPUTS["customized_uv%d" % _i] = "CustomizedUVs%d" % _i
_OUTPUT_NAMES = {}
for _spec_name, _prop in _OUTPUTS.items():
    _OUTPUT_NAMES.setdefault(_prop, _spec_name)

_INPUT_TYPES = {
    "scalar": "FUNCTION_INPUT_SCALAR", "float": "FUNCTION_INPUT_SCALAR", "vector2": "FUNCTION_INPUT_VECTOR2",
    "vector3": "FUNCTION_INPUT_VECTOR3", "vector4": "FUNCTION_INPUT_VECTOR4",
    "texture": "FUNCTION_INPUT_TEXTURE2D", "texture2d": "FUNCTION_INPUT_TEXTURE2D",
    "texture_cube": "FUNCTION_INPUT_TEXTURE_CUBE", "texture2d_array": "FUNCTION_INPUT_TEXTURE2D_ARRAY",
    "volume_texture": "FUNCTION_INPUT_VOLUME_TEXTURE", "static_bool": "FUNCTION_INPUT_STATIC_BOOL",
    "bool": "FUNCTION_INPUT_BOOL", "material_attributes": "FUNCTION_INPUT_MATERIAL_ATTRIBUTES",
}


def _param_spec(name, raw):
    """{'type', 'value', meta...} from a params entry."""
    spec = dict(raw) if isinstance(raw, dict) else {"value": raw}
    value = spec.get("value")
    kind = spec.get("type")
    if kind is None:
        if isinstance(value, bool):
            kind = "switch"
        elif isinstance(value, (int, float)):
            kind = "scalar"
        elif isinstance(value, (list, tuple)):
            kind = "vector"
        elif isinstance(value, str):
            kind = "texture"
        else:
            raise MaterialError("param %r: give a value (number, colour list, texture path or bool) or a 'type'" % name)
    kinds = ("scalar", "vector", "texture", "texture_cube", "switch")
    if kind not in kinds:
        raise MaterialError("param %r: type %r is not one of %s" % (name, kind, ", ".join(kinds)))
    spec["type"] = kind
    return spec


class _Builder:
    def __init__(self, owner, spec, function=False):
        self.owner = owner
        self.function = function
        self.params = {name: _param_spec(name, raw) for name, raw in (spec.get("params") or {}).items()}
        self.lets = dict(spec.get("let") or {})
        self.inputs = {}
        self.cache = {}
        self.let_values = {}
        self.resolving = []
        self.made = 0
        self.where = ""
        self.used = set()

    def fail(self, message):
        raise MaterialError("%s: %s" % (self.where, message) if self.where else message)

    # ---- nodes

    def new(self, cls_name, props=None):
        cls = _expr_class(cls_name)
        if self.function:
            expr = MEL.create_material_expression_in_function(self.owner, cls, 0, 0)
        else:
            expr = MEL.create_material_expression(self.owner, cls, 0, 0)
        if expr is None:
            self.fail("could not create a %s node" % cls_name)
        self.made += 1
        for key, value in (props or {}).items():
            _set_prop(expr, key, value)
        return expr

    def materialize(self, value):
        if isinstance(value, _Ref):
            return value
        key = ("lit", value.v) if isinstance(value, _Lit) else ("named", value.kind, value.name)
        if key in self.cache:
            return self.cache[key]
        if isinstance(value, _Lit):
            v = value.v
            if not isinstance(v, tuple):
                ref = _Ref(self.new("Constant", {"r": v}))
            elif len(v) == 2:
                ref = _Ref(self.new("Constant2Vector", {"r": v[0], "g": v[1]}))
            elif len(v) == 3:
                ref = _Ref(self.new("Constant3Vector", {"constant": list(v) + [1.0]}))
            elif len(v) == 4:
                ref = _Ref(self.new("Constant4Vector", {"constant": list(v)}))
            else:
                self.fail("a constant vector has 2 to 4 numbers, got %d" % len(v))
        elif value.kind in ("texture", "texture_cube"):
            ref = _Ref(self.new("TextureObjectParameter", self.param_props(value.name)))
        elif value.kind == "switch":
            ref = _Ref(self.new("StaticBoolParameter", self.param_props(value.name)))
        elif value.kind == "path":
            obj = _load(value.name)
            if not isinstance(obj, unreal.Texture):
                self.fail("%s is not a texture (only textures can be used as values)" % value.name)
            ref = _Ref(self.new("TextureObject", {"texture": obj}))
        else:
            self.fail("can't use %s %r here" % (value.kind, value.name))
        self.cache[key] = ref
        return ref

    def feed(self, value, expr, pin):
        """Connect a value to an input of expr (a literal number goes into the node's own constant)."""
        if isinstance(value, _Lit):
            prop = _const_prop(expr, pin)
            if isinstance(prop, tuple) and isinstance(value.v, tuple) and len(prop) == len(value.v):
                for p, v in zip(prop, value.v):
                    _set_prop(expr, p, v)
                return
            if isinstance(prop, str) and not isinstance(value.v, tuple):
                _set_prop(expr, prop, value.v)
                return
        ref = self.materialize(value)
        target = "" if pin in ("None", "") else pin
        if not MEL.connect_material_expressions(ref.expr, ref.out, expr, target):
            self.fail("could not connect %s%s to %s's %r input (inputs: %s)" % (
                _class_name(ref.expr), "." + ref.out if ref.out else "", _class_name(expr), pin, _pins(expr)))

    def param_props(self, name):
        self.used.add(name)
        spec = self.params[name]
        props = {"parameter_name": name}
        kind = spec["type"]
        value = spec.get("value")
        if kind == "scalar":
            props["default_value"] = float(value if value is not None else 0)
        elif kind == "vector":
            props["default_value"] = _color(value if value is not None else [0, 0, 0, 1])
        elif kind in ("texture", "texture_cube"):
            if value:
                props["texture"] = _load(value)
        elif kind == "switch":
            props["default_value"] = bool(value)
        for key, prop in (("group", "group"), ("priority", "sort_priority"), ("min", "slider_min"),
                          ("max", "slider_max"), ("desc", "desc")):
            if key in spec:
                props[prop] = spec[key]
        return props

    # ---- expressions

    def run(self, source, where):
        self.where = where
        if isinstance(source, bool):
            return _Lit(1.0 if source else 0.0)
        if isinstance(source, (int, float)):
            return _Lit(float(source))
        if isinstance(source, (list, tuple)):
            return _Lit(tuple(float(v) for v in source))
        try:
            tree = ast.parse(str(source).strip(), mode="eval")
        except SyntaxError as exc:
            self.fail("%s in %r" % (exc.msg, source))
        return self.ev(tree.body)

    def ev(self, node):
        key = ast.dump(node)
        if key in self.cache:
            return self.cache[key]
        value = self._ev(node)
        if isinstance(value, _Ref):
            self.cache[key] = value
        return value

    def _ev(self, node):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return _Lit(1.0 if node.value else 0.0)
            if isinstance(node.value, (int, float)):
                return _Lit(float(node.value))
            if isinstance(node.value, str):
                return _Named("path", node.value)
            self.fail("can't use %r in an expression" % (node.value,))
        if isinstance(node, ast.Name):
            return self.name(node.id)
        if isinstance(node, (ast.Tuple, ast.List)):
            parts = [self.ev(e) for e in node.elts]
            if all(isinstance(p, _Lit) and not isinstance(p.v, tuple) for p in parts):
                return _Lit(tuple(p.v for p in parts))
            return self.append(parts)
        if isinstance(node, ast.BinOp):
            return self.binop(node)
        if isinstance(node, ast.UnaryOp):
            value = self.ev(node.operand)
            if isinstance(node.op, ast.UAdd):
                return value
            if isinstance(node.op, ast.USub):
                if isinstance(value, _Lit):
                    return _Lit(tuple(-v for v in value.v) if isinstance(value.v, tuple) else -value.v)
                expr = self.new("Multiply", {"const_b": -1.0})
                self.feed(value, expr, "A")
                return _Ref(expr)
            self.fail("unsupported operator %s" % type(node.op).__name__)
        if isinstance(node, ast.Attribute):
            base = self.ev(node.value)
            if _SWIZZLE.match(node.attr):
                return self.swizzle(base, node.attr)
            return self.output(base, node.attr)
        if isinstance(node, ast.Call):
            return self.call(node)
        self.fail("unsupported syntax: %s" % ast.dump(node)[:80])

    def name(self, name):
        if name in self.lets:
            if name in self.let_values:
                return self.let_values[name]
            if name in self.resolving:
                self.fail("let %r refers to itself (%s)" % (name, " -> ".join(self.resolving + [name])))
            self.resolving.append(name)
            where = self.where
            value = self.run(self.lets[name], "let %s" % name)
            self.where = where
            self.resolving.pop()
            self.let_values[name] = value
            return value
        if name in self.params:
            return self.param(name)
        if name in self.inputs:
            return self.inputs[name]
        if name in _BUILTINS:
            cls, props, out = _BUILTINS[name]
            return _Ref(self.new(cls, props), out)
        if name == "pi":
            return _Lit(math.pi)
        known = list(self.lets) + list(self.params) + list(self.inputs) + list(_BUILTINS)
        close = difflib.get_close_matches(name, known, 3)
        self.fail("unknown name %r%s" % (name, "; did you mean " + ", ".join(close) if close else ""))

    def param(self, name):
        kind = self.params[name]["type"]
        if kind in ("texture", "texture_cube", "switch"):
            return _Named(kind, name)
        key = ("param", name)
        if key not in self.cache:
            cls = "ScalarParameter" if kind == "scalar" else "VectorParameter"
            self.cache[key] = _Ref(self.new(cls, self.param_props(name)))
        return self.cache[key]

    def binop(self, node):
        a, b = self.ev(node.left), self.ev(node.right)
        op = type(node.op)
        if op not in _BINOPS:
            self.fail("unsupported operator %s" % op.__name__)
        if isinstance(a, _Lit) and isinstance(b, _Lit):
            return self.fold(op, a.v, b.v)
        if op is ast.Sub and isinstance(a, _Lit) and a.v == 1.0:
            expr = self.new("OneMinus")
            self.feed(b, expr, "")
            return _Ref(expr)
        expr = self.new(_BINOPS[op])
        first, second = _pins(expr)[:2]
        self.feed(a, expr, first)
        self.feed(b, expr, second)
        return _Ref(expr)

    def fold(self, op, a, b):
        fn = {ast.Add: lambda x, y: x + y, ast.Sub: lambda x, y: x - y, ast.Mult: lambda x, y: x * y,
              ast.Div: lambda x, y: x / y, ast.Pow: lambda x, y: x ** y, ast.Mod: math.fmod}[op]
        if not isinstance(a, tuple) and not isinstance(b, tuple):
            return _Lit(fn(a, b))
        a = a if isinstance(a, tuple) else (a,) * len(b)
        b = b if isinstance(b, tuple) else (b,) * len(a)
        if len(a) != len(b):
            self.fail("can't combine constants of %d and %d components" % (len(a), len(b)))
        return _Lit(tuple(fn(x, y) for x, y in zip(a, b)))

    def append(self, parts):
        value = parts[0]
        for part in parts[1:]:
            expr = self.new("AppendVector")
            self.feed(value, expr, "A")
            self.feed(part, expr, "B")
            value = _Ref(expr)
        return value

    def swizzle(self, value, letters):
        idx = [_CHANNELS[c] for c in letters]
        if isinstance(value, _Lit):
            v = value.v if isinstance(value.v, tuple) else (value.v,)
            try:
                picked = tuple(v[i] for i in idx)
            except IndexError:
                self.fail(".%s on a %d-component constant" % (letters, len(v)))
            return _Lit(picked[0] if len(picked) == 1 else picked)
        ref = self.materialize(value)
        outs = _outs(ref.expr)
        if not ref.out or (outs and ref.out == outs[0]):
            for name in ("".join("RGBA"[i] for i in idx), "".join("XYZW"[i] for i in idx)):
                if name in outs:
                    return _Ref(ref.expr, name)
        if idx == sorted(set(idx)):
            expr = self.new("ComponentMask", {c: (i in idx) for i, c in enumerate("rgba")})
            self.feed(ref, expr, "")
            return _Ref(expr)
        return self.append([self.swizzle(ref, "rgba"[i]) for i in idx])

    def output(self, value, name):
        ref = self.materialize(value)
        outs = _outs(ref.expr)
        found = _match(outs, name)
        if found is None:
            self.fail("%s has no output %r (outputs: %s)" % (_class_name(ref.expr), name, outs))
        return _Ref(ref.expr, found)

    # ---- calls

    def literal(self, node):
        try:
            return ast.literal_eval(node)
        except ValueError:
            if isinstance(node, ast.Name):
                return node.id
            self.fail("a property value must be a literal, got %s" % ast.dump(node)[:60])

    def call(self, node):
        if not isinstance(node.func, ast.Name):
            self.fail("call a function by name")
        name = node.func.id
        args, kwargs = node.args, [(k.arg, k.value) for k in node.keywords]
        special = getattr(self, "call_" + name, None)
        if special is not None:
            return special(args, kwargs)
        if name in _FUNCS:
            cls, defaults = _FUNCS[name]
            return self.generic(cls, args, kwargs, defaults)
        try:
            _expr_class(name)
        except MaterialError:
            calls = list(_FUNCS) + [n[len("call_"):] for n in dir(self) if n.startswith("call_")]
            close = difflib.get_close_matches(name, calls, 3) or difflib.get_close_matches(name.lower(), calls, 3)
            self.fail("no function or material node %r%s (functions are in the mat docstring; mat.nodes() lists "
                      "node classes)" % (name, "; did you mean " + ", ".join(close) if close else ""))
        return self.generic(name, args, kwargs)

    def generic(self, cls_name, args, kwargs, defaults=None):
        expr = self.new(cls_name, defaults)
        pins = _pins(expr)
        if len(args) > len(pins):
            self.fail("%s takes %d inputs (%s), got %d" % (_class_name(expr), len(pins), pins, len(args)))
        inputs = [(pin, arg) for pin, arg in zip(pins, args)
                  if not (isinstance(arg, ast.Constant) and arg.value is None)]
        for key, value in kwargs:
            pin = _match(pins, key)
            if pin is None and key == "normal":
                pin = _match(pins, "Normal")
            alias = {"exponent": "ExponentIn", "base": "BaseReflectFractionIn", "uv": "Coordinate"}.get(key)
            if pin is None and alias:
                pin = _match(pins, alias)
            if pin is not None:
                inputs.append((pin, value))
            elif _prop_name(expr, key) is None:
                self.fail("%s has no input or property %r (inputs: %s)" % (_class_name(expr), key, ", ".join(pins)))
            else:
                _set_prop(expr, key, self.literal(value))
        for pin, arg in inputs:
            self.feed(self.ev(arg), expr, pin)
        return _Ref(expr)

    def call_param(self, args, kwargs):
        name = self.literal(args[0]) if args else None
        if name not in self.params:
            self.fail("param(%r): no such parameter (params: %s)" % (name, ", ".join(self.params)))
        return self.param(name)

    def call_input(self, args, kwargs):
        name = self.literal(args[0]) if args else None
        if name not in self.inputs:
            self.fail("input(%r): no such function input (inputs: %s)" % (name, ", ".join(self.inputs)))
        return self.inputs[name]

    def call_sample(self, args, kwargs):
        if not args:
            self.fail("sample(texture, uv)")
        kw = dict(kwargs)
        tex = self.ev(args[0])
        uv = args[1] if len(args) > 1 else kw.pop("uv", None)
        sampler = self.literal(kw.pop("sampler")) if "sampler" in kw else None
        if isinstance(tex, _Named) and tex.kind in ("texture", "texture_cube"):
            cls = "TextureSampleParameterCube" if tex.kind == "texture_cube" else "TextureSampleParameter2D"
            expr = self.new(cls, self.param_props(tex.name))
        elif isinstance(tex, _Named) and tex.kind == "path":
            expr = self.new("TextureSample", {"texture": _load(tex.name)})
        elif isinstance(tex, _Ref):
            expr = self.new("TextureSample")
            self.feed(tex, expr, "Tex")
        else:
            self.fail("sample() takes a texture param, a texture path or a texture object")
        if sampler is not None:
            _set_prop(expr, "sampler_type", sampler)
        for key, value in kw.items():
            _set_prop(expr, key, self.literal(value))
        if uv is not None:
            self.feed(self.ev(uv), expr, "UVs")
        return _Ref(expr)

    def call_switch(self, args, kwargs):
        if len(args) != 3:
            self.fail("switch(Param, on_true, on_false)")
        param = self.ev(args[0])
        if not (isinstance(param, _Named) and param.kind == "switch"):
            self.fail("switch() needs a bool parameter first (params: {'Name': True})")
        expr = self.new("StaticSwitchParameter", self.param_props(param.name))
        self.feed(self.ev(args[1]), expr, "True")
        self.feed(self.ev(args[2]), expr, "False")
        return _Ref(expr)

    def call_append(self, args, kwargs):
        return self.append([self.ev(a) for a in args])

    def call_scene_texture(self, args, kwargs):
        kw = dict(kwargs)
        which = self.literal(args[0]) if args else "scene_color"
        expr = self.new("SceneTexture", {"scene_texture_id": which})
        uv = args[1] if len(args) > 1 else kw.pop("uv", None)
        for key, value in kw.items():
            _set_prop(expr, key, self.literal(value))
        if uv is not None:
            self.feed(self.ev(uv), expr, "UVs")
        return _Ref(expr)

    def call_custom(self, args, kwargs):
        if not args:
            self.fail("custom('hlsl code', out='float3', Input=value, ...)")
        code = self.literal(args[0])
        kw = dict(kwargs)
        out = str(self.literal(kw.pop("out"))) if "out" in kw else "float3"
        desc = self.literal(kw.pop("desc")) if "desc" in kw else None
        props = self.literal(kw.pop("props")) if "props" in kw else {}
        types = {"float": "CMOT_FLOAT1", "float1": "CMOT_FLOAT1", "float2": "CMOT_FLOAT2", "float3": "CMOT_FLOAT3",
                 "float4": "CMOT_FLOAT4", "attributes": "CMOT_MATERIAL_ATTRIBUTES"}
        if out.lower() not in types:
            self.fail("custom(out=...) is one of %s" % ", ".join(types))
        expr = self.new("Custom", {"code": code, "output_type": types[out.lower()]})
        if desc:
            _set_prop(expr, "description", desc)
        for key, value in props.items():
            _set_prop(expr, key, value)
        names = [k for k, _ in kwargs if k not in ("out", "desc", "props")]
        made = []
        for n in names:
            item = unreal.CustomInput()
            item.set_editor_property("input_name", unreal.Name(n))
            made.append(item)
        expr.set_editor_property("inputs", made)
        for n in names:
            self.feed(self.ev(kw[n]), expr, n)
        return _Ref(expr)

    def call_fn(self, args, kwargs):
        if not args:
            self.fail("fn('/Game/MF_Name', inputs...)")
        function = _load(self.literal(args[0]))
        if not isinstance(function, unreal.MaterialFunctionInterface):
            self.fail("%s is not a material function" % args[0])
        expr = self.new("MaterialFunctionCall")
        expr.set_editor_property("material_function", function)
        pins = _pins(expr)
        rest = args[1:]
        if len(rest) > len(pins):
            self.fail("%s takes %d inputs (%s)" % (function.get_name(), len(pins), pins))
        for pin, arg in zip(pins, rest):
            if not (isinstance(arg, ast.Constant) and arg.value is None):
                self.feed(self.ev(arg), expr, pin)
        for key, value in kwargs:
            pin = _match(pins, key)
            if pin is None:
                self.fail("%s has no input %r (inputs: %s)" % (function.get_name(), key, pins))
            self.feed(self.ev(value), expr, pin)
        return _Ref(expr)

    def _transform(self, cls, args, kwargs):
        if not args:
            self.fail("transform(value, 'from', 'to')")
        kw = dict(kwargs)
        src = self.literal(args[1]) if len(args) > 1 else kw.pop("src", "tangent" if cls == "Transform" else "local")
        dst = self.literal(args[2]) if len(args) > 2 else kw.pop("dst", "world")
        expr = self.new(cls, {"transform_source_type": src, "transform_type": dst})
        self.feed(self.ev(args[0]), expr, "")
        return _Ref(expr)

    def call_transform(self, args, kwargs):
        return self._transform("Transform", args, kwargs)

    def call_transform_position(self, args, kwargs):
        return self._transform("TransformPosition", args, kwargs)


# --------------------------------------------------------------------------- material outputs

def _connect_output(material, ref, name, where):
    prop = _OUTPUTS.get(name) or {_norm(k): v for k, v in _OUTPUTS.items()}.get(_norm(name))
    if prop is None:
        close = difflib.get_close_matches(name, list(_OUTPUTS), 3)
        raise MaterialError("%s: no material output %r%s" % (where, name, "; did you mean " + ", ".join(close) if close else ""))
    enum = getattr(unreal.MaterialProperty, "MP_" + _snake(prop).upper(), None)
    if enum is not None and MEL.connect_material_property(ref.expr, ref.out, enum):
        return
    outs = _outs(ref.expr)
    index = outs.index(ref.out) if ref.out in outs else 0
    _call("connect_property", material, ref.expr, index, prop)


# --------------------------------------------------------------------------- building

def _existing(path, cls):
    package = path.split(".")[0]
    if not EAL.does_asset_exist(package):
        return None
    obj = EAL.load_asset(package)
    if not isinstance(obj, cls):
        raise MaterialError("%s already exists and is a %s, not a %s" % (package, obj.get_class().get_name(), cls.__name__))
    return obj


def _create(path, cls, factory):
    package = path.split(".")[0]
    folder, name = package.rsplit("/", 1)
    obj = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, cls, factory)
    if obj is None:
        raise MaterialError("could not create %s" % package)
    return obj


def _clear(owner, function=False):
    """Delete every node. The engine's delete_all_material_expressions removes items from the list it loops over,
    so it skips about half of them (UE 5.8); delete from a copy until none are left."""
    delete = MEL.delete_material_expression_in_function if function else MEL.delete_material_expression
    for _ in range(8):
        exprs = _expressions(owner, function)
        if not exprs:
            return
        for expr in exprs:
            delete(owner, expr)
    raise MaterialError("could not delete the old nodes of %s" % owner.get_path_name())


def _save(obj):
    return bool(unreal.EditorLoadingAndSavingUtils.save_packages([obj.get_outermost()], False))


def compile(target, wait=True):
    """Compile errors of a material or instance: {'errors': [...], 'error_nodes': [...], 'finished': bool}.
    wait=True waits for the shaders (Custom HLSL errors only show up then). finished=False with no errors means it
    wasn't compiled (headless runs compile no shaders). Needs the UETKEditor plugin."""
    obj = _asset(target)
    return _call("compile_result", obj, bool(wait))


def stats(target):
    """Instruction and sampler counts of a material."""
    s = MEL.get_statistics(_asset(target))
    return {"pixel_instructions": s.get_editor_property("num_pixel_shader_instructions"),
            "vertex_instructions": s.get_editor_property("num_vertex_shader_instructions"),
            "samplers": s.get_editor_property("num_samplers")}


def _finish(obj, result, save_asset, check):
    """errors: the compiler's messages, [] = compiled cleanly, None = not checked (no plugin, or nothing compiles
    shaders here: a headless run). ok follows: True, False, or None when not checked."""
    if check and _plugin() is not None:
        compiled = compile(obj, wait=True)
        if compiled["errors"]:
            result["errors"] = compiled["errors"]
            result["error_nodes"] = compiled["error_nodes"]
        elif compiled.get("finished"):
            result["errors"] = []
        else:
            result["errors"] = None
            result["note"] = ("not compiled here (%s; headless runs compile no shaders): check it in a running editor "
                              "with `ue.py mat compile PATH`" % compiled.get("note", "unfinished"))
    elif check:
        result["errors"] = None
        result["note"] = "install the UETKEditor plugin to get compile errors (ue.py plugins install)"
    result["ok"] = None if result.get("errors") is None else not result["errors"]
    if save_asset:
        result["saved"] = _save(obj)
    return result


def build(spec, path, save_asset=True, check=True):
    """Create the material at `path` from a spec (dict or JSON text; see the module doc), or rebuild it in place
    (every node replaced; references to it stay valid). Compiles, and saves unless save_asset=False.
    Returns {path, nodes, params, errors (compile errors; [] = none), stats, saved}."""
    if isinstance(spec, str):
        spec = json.loads(spec)
    if spec.get("function"):
        return function(spec, path, save_asset=save_asset)
    material = _existing(path, unreal.Material)
    if material is None:
        material = _create(path, unreal.Material, unreal.MaterialFactoryNew())
    else:
        _clear(material)
        if _plugin() is not None:
            # Back to a fresh material: no outputs connected, every setting at the engine's default.
            for prop in set(_OUTPUTS.values()):
                try:
                    _call("connect_property", material, None, 0, prop)
                except MaterialError:
                    pass
            for key in _call("describe_graph", material).get("props", {}):
                if key not in _MATERIAL_SKIP:
                    try:
                        material.reset_editor_property(_prop_name(material, key) or key)
                    except Exception:
                        pass

    _set_prop(material, "material_domain", spec.get("domain", "surface"))
    _set_prop(material, "blend_mode", spec.get("blend", "opaque"))
    _set_prop(material, "shading_model", spec.get("shading", "default_lit"))
    _set_prop(material, "two_sided", bool(spec.get("two_sided", False)))
    for key, value in (spec.get("properties") or {}).items():
        _set_prop(material, key, value)
    for usage in spec.get("usage") or []:
        MEL.set_material_usage(material, _enum_value(unreal.MaterialUsage, usage))

    b = _Builder(material, spec)
    for name, source in (spec.get("out") or {}).items():
        value = b.run(source, "out %s" % name)
        _connect_output(material, b.materialize(value), name, "out %s" % name)
    for name in b.lets:
        b.name(name)  # build unused lets too, so their mistakes show up now
    for name in b.params:
        if name not in b.used:  # declared but unused: still a parameter instances can set
            b.materialize(b.param(name))

    MEL.layout_material_expressions(material)
    MEL.recompile_material(material)
    result = {"path": material.get_path_name().split(".")[0], "nodes": len(_expressions(material)),
              "params": sorted(b.params)}
    _finish(material, result, save_asset, check)
    try:
        result["stats"] = stats(material)
    except Exception:
        pass
    return result


def function(spec, path, save_asset=True):
    """Create or rebuild a material function from a spec (module doc, FUNCTION SPEC). Saves unless save_asset=False.
    Compile errors show in the materials that call it."""
    if isinstance(spec, str):
        spec = json.loads(spec)
    func = _existing(path, unreal.MaterialFunction)
    if func is None:
        func = _create(path, unreal.MaterialFunction, unreal.MaterialFunctionFactoryNew())
    else:
        _clear(func, function=True)
    if "description" in spec:
        _set_prop(func, "description", spec["description"])
    if "expose" in spec:
        _set_prop(func, "expose_to_library", bool(spec["expose"]))
    if "categories" in spec:
        func.set_editor_property("library_categories_text", [unreal.Text(c) for c in spec["categories"]])

    b = _Builder(func, spec, function=True)
    for priority, (name, raw) in enumerate((spec.get("inputs") or {}).items()):
        info = dict(raw) if isinstance(raw, dict) else {"type": raw}
        kind = _INPUT_TYPES.get(str(info.get("type", "vector3")).lower())
        if kind is None:
            raise MaterialError("input %r: type %r is not one of %s" % (name, info.get("type"), ", ".join(_INPUT_TYPES)))
        props = {"input_name": name, "input_type": kind, "sort_priority": priority}
        if "desc" in info:
            props["description"] = info["desc"]
        expr = b.new("FunctionInput", props)
        if "default" in info:
            d = info["default"]
            d = list(d) if isinstance(d, (list, tuple)) else [float(d)]
            _set_prop(expr, "preview_value", d + [0.0] * (4 - len(d)))
            expr.set_editor_property("use_preview_value_as_default", True)
        b.inputs[name] = _Ref(expr)
    for priority, (name, source) in enumerate((spec.get("out") or {}).items()):
        value = b.run(source, "out %s" % name)
        out = b.new("FunctionOutput", {"output_name": name, "sort_priority": priority})
        b.where = "out %s" % name
        b.feed(value, out, "")
    for name in b.lets:
        b.name(name)
    MEL.update_material_function(func, None)
    MEL.layout_material_function_expressions(func)
    result = {"path": func.get_path_name().split(".")[0], "nodes": len(_expressions(func, True)),
              "inputs": list(b.inputs), "outputs": list(spec.get("out") or {})}
    if save_asset:
        result["saved"] = _save(func)
    return result


# --------------------------------------------------------------------------- instances and parameters

_KINDS = ("scalar", "vector", "texture", "static_switch")


def _param_kinds(material):
    kinds = {}
    for kind in _KINDS:
        for name in getattr(MEL, "get_%s_parameter_names" % kind)(material):
            kinds[str(name)] = kind
    return kinds


def _jsonable(value):
    if isinstance(value, unreal.LinearColor):
        return [round(value.r, 6), round(value.g, 6), round(value.b, 6), round(value.a, 6)]
    if isinstance(value, unreal.Object):
        return _short_path(value.get_path_name())
    if isinstance(value, float):
        return round(value, 6)
    return value


def params(target):
    """Every parameter of a material or material instance: {name: {type, value, overridden (instances)}}."""
    obj = _asset(target)
    instance = isinstance(obj, unreal.MaterialInstance)
    out = {}
    for name, kind in sorted(_param_kinds(obj).items()):
        if instance:
            value = getattr(MEL, "get_material_instance_%s_parameter_value" % kind)(obj, name)
        else:
            value = getattr(MEL, "get_material_default_%s_parameter_value" % kind)(obj, name)
        entry = {"type": kind.replace("static_", ""), "value": _jsonable(value)}
        if instance:
            try:
                entry["overridden"] = bool(MEL.is_material_instance_parameter_overridden(obj, name))
            except Exception:
                pass
        out[name] = entry
    return out


def instance(path, parent=None, params=None, save_asset=True, reset=False, check=True):
    """Create or update a material instance: parent (a material or instance) is required when creating; params
    {name: value} sets overrides (numbers, colour lists, texture paths, bools for static switches); reset=True
    clears every override first. Saves unless save_asset=False."""
    mi = _existing(path, unreal.MaterialInstanceConstant)
    if mi is None:
        if parent is None:
            raise MaterialError("a new instance needs a parent material")
        mi = _create(path, unreal.MaterialInstanceConstant, unreal.MaterialInstanceConstantFactoryNew())
    if parent is not None:
        MEL.set_material_instance_parent(mi, _asset(parent))
    if mi.get_editor_property("parent") is None:
        raise MaterialError("%s has no parent material" % path)
    if reset:
        MEL.clear_all_material_instance_parameters(mi)
    kinds = _param_kinds(mi)
    for name, value in (params or {}).items():
        real = name if name in kinds else _match(kinds, name)
        if real is None:
            raise MaterialError("%s has no parameter %r (parameters: %s)" % (
                path, name, ", ".join("%s (%s)" % kv for kv in sorted(kinds.items()))))
        kind = kinds[real]
        # The engine's setters always return False (UE 5.8 MaterialEditingLibrary), so check by reading back.
        wanted = {"scalar": float, "vector": _color, "texture": _load, "static_switch": bool}[kind](value)
        getattr(MEL, "set_material_instance_%s_parameter_value" % kind)(mi, real, wanted)
        got = getattr(MEL, "get_material_instance_%s_parameter_value" % kind)(mi, real)
        if _jsonable(got) != _jsonable(wanted):
            raise MaterialError("could not set %s = %r (it reads back as %r)" % (real, value, _jsonable(got)))
    MEL.update_material_instance(mi)
    result = {"path": mi.get_path_name().split(".")[0],
              "parent": _short_path(mi.get_editor_property("parent").get_path_name()),
              "set": sorted((params or {}).keys())}
    return _finish(mi, result, save_asset, check)


# --------------------------------------------------------------------------- reading back

_CHEAP = {"ScalarParameter", "VectorParameter", "StaticBoolParameter", "TextureObjectParameter", "Constant",
          "Constant2Vector", "Constant3Vector", "Constant4Vector", "FunctionInput", "TextureObject"}
_PARAM_CLASSES = {"ScalarParameter": "scalar", "VectorParameter": "vector", "StaticBoolParameter": "switch",
                  "StaticSwitchParameter": "switch", "TextureObjectParameter": "texture",
                  "TextureSampleParameter2D": "texture", "TextureSampleParameterCube": "texture_cube"}
_BUILTIN_BY_CLASS = {}
for _name, (_cls, _props, _out) in _BUILTINS.items():
    if not _props and _name not in ("uv0",):
        _BUILTIN_BY_CLASS.setdefault((_cls, _out), _name)
_SWIZZLE_OUTS = {"R", "G", "B", "A", "RGB", "RGBA", "XYZ", "XY", "Z"}
_MATERIAL_SKIP = {"MaterialDomain", "BlendMode", "ShadingModel", "TwoSided", "ShadingModels", "StateId",
                  "ThumbnailInfo", "AssetImportData", "ParameterGroupData", "bTwoSided", "PreviewMesh",
                  "CachedExpressionData", "EditorComments", "EditorOnlyData"}
_PIN_NAMES = {"UVs": "tex_uv", "Coordinate": "tex_uv", "Alpha": "alpha", "Normal": "normal", "Tex": "texture"}
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED = set(_BUILTINS) | set(_FUNCS) | {"pi", "param", "input", "sample", "switch", "append", "scene_texture",
                                             "custom", "fn", "transform", "transform_position"}


def _clean(value):
    """Floats rounded to 6 significant digits (float32 noise: 0.4000000059604645 -> 0.4), recursively."""
    if isinstance(value, float):
        return float(format(value, ".6g"))
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    return value


def _num(v):
    if isinstance(v, bool):
        return "True" if v else "False"
    text = format(float(v), ".6g")
    return text


def _lit(value):
    if isinstance(value, (list, tuple)):
        return "(%s)" % ", ".join(_num(v) for v in value)
    if isinstance(value, (int, float)):
        return _num(value)
    return repr(value)


def _paren(text_prec, need):
    text, prec = text_prec
    return text if prec >= need else "(%s)" % text


class _Decompiler:
    def __init__(self, owner):
        self.owner = owner
        self.graph = _call("describe_graph", owner)
        self.function = self.graph["kind"] == "function"
        self.nodes = {n["id"]: n for n in self.graph["nodes"]}
        self.objs = {e.get_name(): e for e in _expressions(owner, self.function)}
        self.params = {}
        self.inputs = []
        self.uses = {}
        self.feeds = {}
        self.let_names = {}
        self.lets = []
        self.done = set()
        self.taken = set()

    # ---- graph walking

    def follow(self, node_id, output):
        seen = 0
        while node_id in self.nodes and seen < 64:
            n = self.nodes[node_id]
            if n["class"] in ("Reroute", "NamedRerouteDeclaration") and n["inputs"] and n["inputs"][0]["node"]:
                node_id, output = n["inputs"][0]["node"], n["inputs"][0]["output"]
            elif n["class"] == "NamedRerouteUsage" and n["props"].get("Declaration"):
                node_id, output = re.split(r"[.:]", n["props"]["Declaration"])[-1], 0
            else:
                break
            seen += 1
        return node_id, output

    def roots(self):
        if self.function:
            outs = [n for n in self.nodes.values() if n["class"] == "FunctionOutput"]
            outs.sort(key=lambda n: (self.prop(n, "sort_priority") or 0, n["y"]))
            return [(self.prop(n, "output_name"), n["inputs"][0]) for n in outs if n["inputs"]]
        return [(_OUTPUT_NAMES.get(o["property"], _snake(o["property"])), {"node": o["node"], "output": o["output"]})
                for o in self.graph.get("outputs", [])]

    def count(self, node_id, output, pin=None):
        node_id, _ = self.follow(node_id, output)
        if node_id not in self.nodes:
            return
        self.uses[node_id] = self.uses.get(node_id, 0) + 1
        self.feeds.setdefault(node_id, set()).add(pin)
        if self.uses[node_id] > 1:
            return
        for inp in self.nodes[node_id]["inputs"]:
            if inp["node"]:
                self.count(inp["node"], inp["output"], inp["name"])

    def prop(self, n, name, default=None):
        obj = self.objs.get(n["id"])
        if obj is None:
            return default
        used = _prop_name(obj, name)
        if used is None:
            return default
        value = obj.get_editor_property(used)
        if isinstance(value, unreal.Name):
            value = str(value)
        if isinstance(value, unreal.Text):
            value = str(value)
        return value

    # ---- text

    def run(self):
        roots = self.roots()
        for name, link in roots:
            if link.get("node"):
                self.count(link["node"], link["output"], name)
        out = {}
        for name, link in roots:
            if link.get("node"):
                out[name] = self.ref(link["node"], link["output"])[0]
        unused = [nid for nid, n in self.nodes.items() if nid not in self.uses
                  and n["class"] not in ("Comment", "FunctionOutput", "Reroute", "NamedRerouteDeclaration",
                                         "NamedRerouteUsage", "FunctionInput")
                  and n["class"] not in _PARAM_CLASSES]
        return out, unused

    def name_for(self, n):
        base = {"LinearInterpolate": "lerp", "TextureSample": "tex", "TextureSampleParameter2D": "tex",
                "MaterialFunctionCall": "fn"}.get(n["class"]) or _snake(n["class"])
        pins = self.feeds.get(n["id"], set())
        if len(pins) == 1 and next(iter(pins)) in _PIN_NAMES:
            base = _PIN_NAMES[next(iter(pins))]
        if n["class"] in ("TextureSampleParameter2D", "TextureSampleParameterCube"):
            pname = self.prop(n, "parameter_name")
            if pname and _IDENT.match(_snake(pname.replace(" ", ""))):
                base = _snake(pname.replace(" ", "")) + "_tex"
        name, k = base, 1
        while name in self.taken or name in _RESERVED or name in self.params:
            k += 1
            name = "%s%d" % (base, k)
        self.taken.add(name)
        return name

    def ref(self, node_id, output):
        node_id, output = self.follow(node_id, output)
        n = self.nodes.get(node_id)
        if n is None:
            return ("None", 100)
        if self.uses.get(node_id, 0) > 1 and n["class"] not in _CHEAP and not self.builtin(n):
            if node_id not in self.let_names:
                text = self.node(n)[0]
                self.let_names[node_id] = self.name_for(n)
                self.lets.append((self.let_names[node_id], text))
            base = (self.let_names[node_id], 100)
        else:
            base = self.node(n)
        if output == 0:
            return base
        outs = n["outputs"]
        info = outs[output] if output < len(outs) else {"name": "", "mask": ""}
        name = info["name"] or info["mask"]
        if n["class"] == "ScreenPosition" and output == 1 and base[0] == "screen_uv":
            return ("pixel_pos", 100)
        if name.upper() in _SWIZZLE_OUTS:
            return ("%s.%s" % (_paren(base, 100), name.lower()), 100)
        return ("%s.%s" % (_paren(base, 100), name), 100)

    def inp(self, n, i):
        """Text of input i: its link, the node's own constant, or None."""
        link = n["inputs"][i]
        if link["node"]:
            return self.ref(link["node"], link["output"])
        obj = self.objs.get(n["id"])
        prop = _const_prop(obj, link["name"]) if obj is not None else None
        if isinstance(prop, tuple):
            return (_lit([self.prop(n, p) for p in prop]), 100)
        if prop:
            return (_num(self.prop(n, prop)), 100)
        return None

    def builtin(self, n):
        if n["props"] and set(n["props"]) - {"CoordinateIndex"}:
            return None
        if n["class"] == "TextureCoordinate":
            index = int(n["props"].get("CoordinateIndex", 0))
            return "uv" if index == 0 else "uv%d" % index
        return _BUILTIN_BY_CLASS.get((n["class"], "ViewportUV" if n["class"] == "ScreenPosition" else ""))

    def param_name(self, n, kind):
        name = self.prop(n, "parameter_name") or "Param"
        if name not in self.params:
            entry = {"type": kind}
            if kind == "scalar":
                entry["value"] = round(self.prop(n, "default_value"), 6)
            elif kind == "vector":
                entry["value"] = _jsonable(self.prop(n, "default_value"))
            elif kind in ("texture", "texture_cube"):
                tex = self.prop(n, "texture")
                entry["value"] = _short_path(tex.get_path_name()) if tex else None
            elif kind == "switch":
                entry["value"] = bool(self.prop(n, "default_value"))
            for key, prop in (("Group", "group"), ("SortPriority", "priority"), ("SliderMin", "min"),
                              ("SliderMax", "max")):
                if key in n["props"]:
                    entry[prop] = n["props"][key]
            if n.get("desc"):
                entry["desc"] = n["desc"]
            self.params[name] = entry
        return name if _IDENT.match(name) and name not in _RESERVED else "param(%r)" % name

    def call(self, fname, n, keywords=(), skip_props=()):
        """fname(pins..., Prop=value): leading present pins positionally, later ones by keyword."""
        items = [(re.sub(r"[^0-9A-Za-z_]", "", link["name"] or ""), self.inp(n, i))
                 for i, link in enumerate(n["inputs"])]
        while items and items[-1][1] is None:
            items.pop()
        first_gap = next((i for i, (_, text) in enumerate(items) if text is None), len(items))
        later = items[first_gap:]
        by_keyword = all(_IDENT.match(key) for key, text in later if text is not None)
        parts = [text[0] for _, text in items[:first_gap]]
        for key, text in later:
            if text is None:
                if not by_keyword:
                    parts.append("None")
            elif by_keyword:
                parts.append("%s=%s" % (key, text[0]))
            else:
                parts.append(text[0])
        obj = self.objs.get(n["id"])
        consts = set()
        for link in n["inputs"]:
            prop = _const_prop(obj, link["name"]) if obj is not None else None
            consts.update(prop if isinstance(prop, tuple) else [prop] if prop else [])
        for key, value in n["props"].items():
            if key in skip_props or _snake(key) in consts:
                continue
            parts.append("%s=%s" % (key, _lit(value)))
        parts.extend(keywords)
        return ("%s(%s)" % (fname, ", ".join(parts)), 100)

    def node(self, n):
        cls = n["class"]
        b = self.builtin(n)
        if b:
            return (b, 100)
        if cls in ("Add", "Subtract", "Multiply", "Divide"):
            a, c = self.inp(n, 0), self.inp(n, 1)
            op, prec = {"Add": ("+", 50), "Subtract": ("-", 50), "Multiply": ("*", 60), "Divide": ("/", 60)}[cls]
            if cls == "Multiply" and c and c[0] == "-1" and a:
                return ("-%s" % _paren(a, 70), 70)
            return ("%s %s %s" % (_paren(a, prec), op, _paren(c, prec + 1)), prec)
        if cls == "OneMinus":
            return ("1 - %s" % _paren(self.inp(n, 0) or ("0", 100), 51), 50)
        if cls == "Power":
            return ("pow(%s, %s)" % (self.inp(n, 0)[0], self.inp(n, 1)[0]), 100)
        if cls == "Constant":
            return (_num(self.prop(n, "r")), 100)
        if cls == "Constant2Vector":
            return (_lit([self.prop(n, "r"), self.prop(n, "g")]), 100)
        if cls in ("Constant3Vector", "Constant4Vector"):
            c = self.prop(n, "constant")
            values = [c.r, c.g, c.b] + ([c.a] if cls == "Constant4Vector" else [])
            return (_lit(values), 100)
        if cls == "ComponentMask":
            letters = "".join(c for c in "rgba" if self.prop(n, c))
            return ("%s.%s" % (_paren(self.inp(n, 0) or ("0", 100), 100), letters), 100)
        if cls == "AppendVector":
            items = []
            for i in (0, 1):
                link = n["inputs"][i]
                nid, out = self.follow(link["node"], link["output"]) if link["node"] else (None, 0)
                sub = self.nodes.get(nid)
                if sub and sub["class"] == "AppendVector" and out == 0 and self.uses.get(nid, 0) <= 1:
                    inner = self.node(sub)[0]
                    items.append(inner[1:-1])
                else:
                    items.append((self.inp(n, i) or ("0", 100))[0])
            return ("(%s)" % ", ".join(items), 100)
        if cls in ("ScalarParameter", "VectorParameter", "StaticBoolParameter", "TextureObjectParameter"):
            return (self.param_name(n, _PARAM_CLASSES[cls]), 100)
        if cls in ("TextureSampleParameter2D", "TextureSampleParameterCube"):
            name = self.param_name(n, _PARAM_CLASSES[cls])
            uv = self.inp(n, 0)
            return ("sample(%s%s)" % (name, ", " + uv[0] if uv else ""), 100)
        if cls == "TextureSample":
            uv = self.inp(n, 0)
            tex_link = n["inputs"][1] if len(n["inputs"]) > 1 else None
            if tex_link and tex_link["node"]:
                tex = self.ref(tex_link["node"], tex_link["output"])[0]
            else:
                texture = self.prop(n, "texture")
                tex = repr(_short_path(texture.get_path_name())) if texture else "None"
            return ("sample(%s%s)" % (tex, ", " + uv[0] if uv else ""), 100)
        if cls == "TextureObject":
            texture = self.prop(n, "texture")
            return (repr(_short_path(texture.get_path_name())) if texture else "None", 100)
        if cls == "StaticSwitchParameter":
            name = self.param_name(n, "switch")
            on, off = self.inp(n, 0), self.inp(n, 1)
            return ("switch(%s, %s, %s)" % (name, (on or ("0", 0))[0], (off or ("0", 0))[0]), 100)
        if cls in ("Sine", "Cosine"):
            period = self.prop(n, "period")
            extra = [] if not period else ["period=%s" % _num(period)]
            return self.call("sin" if cls == "Sine" else "cos", n, extra, skip_props={"Period"})
        if cls == "Custom":
            out = _enum_name(self.prop(n, "output_type"))
            out = {"float1": "float"}.get(out, out)
            parts = [repr(self.prop(n, "code"))]
            if out != "float3":
                parts.append("out=%r" % out)
            desc = self.prop(n, "description")
            if desc and desc != "Custom":
                parts.append("desc=%r" % desc)
            extra = {k: v for k, v in n["props"].items() if k not in ("Code", "OutputType", "Description")}
            if extra:
                parts.append("props=%r" % extra)
            for i, link in enumerate(n["inputs"]):
                text = self.inp(n, i)
                if text:
                    parts.append("%s=%s" % (link["name"], text[0]))
            return ("custom(%s)" % ", ".join(parts), 100)
        if cls == "MaterialFunctionCall":
            func = self.prop(n, "material_function")
            path = repr(_short_path(func.get_path_name())) if func else "None"
            inner = self.call("fn", n, skip_props={"MaterialFunction"})[0][len("fn("):-1]
            return ("fn(%s%s)" % (path, ", " + inner if inner else ""), 100)
        if cls == "SceneTexture":
            which = _enum_name(self.prop(n, "scene_texture_id"))
            uv = self.inp(n, 0)
            return ("scene_texture(%r%s)" % (which, ", " + uv[0] if uv else ""), 100)
        if cls in ("Transform", "TransformPosition"):
            src = _enum_name(self.prop(n, "transform_source_type"))
            dst = _enum_name(self.prop(n, "transform_type"))
            fname = "transform" if cls == "Transform" else "transform_position"
            return ("%s(%s, %r, %r)" % (fname, (self.inp(n, 0) or ("0", 0))[0], src, dst), 100)
        if cls == "FunctionInput":
            name = self.prop(n, "input_name")
            return (name if _IDENT.match(name) and name not in _RESERVED else "input(%r)" % name, 100)
        if cls in _FUNC_BY_CLASS:
            return self.call(_FUNC_BY_CLASS[cls], n)
        return self.call(cls, n)

    def spec(self):
        out, unused = self.run()
        for n in self.nodes.values():  # parameters connected to nothing are still parameters
            if n["class"] in _PARAM_CLASSES and n["id"] not in self.uses:
                self.param_name(n, _PARAM_CLASSES[n["class"]])
        spec = {}
        lets = dict(self.lets)
        if self.function:
            inputs = {}
            nodes = sorted((n for n in self.nodes.values() if n["class"] == "FunctionInput"),
                           key=lambda n: (self.prop(n, "sort_priority") or 0, n["y"]))
            for n in nodes:
                info = {"type": _enum_name(self.prop(n, "input_type"), strip_prefix=False)
                        .replace("function_input_", "")}
                if self.prop(n, "use_preview_value_as_default"):
                    v = self.prop(n, "preview_value")
                    values = [round(v.get_editor_property(f), 6) for f in "xyzw"]
                    size = {"scalar": 1, "vector2": 2, "vector3": 3, "bool": 1, "static_bool": 1}.get(info["type"], 4)
                    info["default"] = values[0] if size == 1 else values[:size]
                desc = self.prop(n, "description")
                if desc:
                    info["desc"] = desc
                inputs[self.prop(n, "input_name")] = info if len(info) > 1 else info["type"]
            spec["function"] = True
            props = self.graph.get("props", {})
            if props.get("Description"):
                spec["description"] = props["Description"]
            if "bExposeToLibrary" in props:
                spec["expose"] = props["bExposeToLibrary"]
            spec["inputs"] = inputs
        else:
            m = self.owner
            spec["domain"] = _enum_name(m.get_editor_property("material_domain"))
            spec["blend"] = _enum_name(m.get_editor_property("blend_mode"))
            spec["shading"] = _enum_name(m.get_editor_property("shading_model"))
            if m.get_editor_property("two_sided"):
                spec["two_sided"] = True
            usage, props = [], {}
            for key, value in self.graph.get("props", {}).items():
                if key in _MATERIAL_SKIP:
                    continue
                if key.startswith("bUsedWith") and value is True:
                    usage.append(_snake(key[len("bUsedWith"):]))
                    continue
                props[key] = _clean(value)
            if usage:
                spec["usage"] = usage
            if props:
                spec["properties"] = props
        if self.params:
            spec["params"] = {k: (v["value"] if set(v) == {"type", "value"} and v["type"] != "texture_cube"
                                  and v["value"] is not None else v) for k, v in self.params.items()}
        if lets:
            spec["let"] = lets
        spec["out"] = out
        if unused:
            spec["unused_nodes"] = sorted("%s (%s)" % (nid, self.nodes[nid]["class"]) for nid in unused)
        return spec


def _instance_spec(mi):
    parent = mi.get_editor_property("parent")
    chain, p = [], parent
    while p is not None:
        chain.append(_short_path(p.get_path_name()))
        p = p.get_editor_property("parent") if isinstance(p, unreal.MaterialInstance) else None
    all_params = params(mi)
    overrides = {k: v["value"] for k, v in all_params.items() if v.get("overridden")}
    return {"instance_of": chain, "set": overrides,
            "params": {k: v["value"] for k, v in all_params.items()}}


def describe(target, text=True):
    """A material, material function or instance as its spec: build()/function()/instance() take it back.
    text=True returns readable text, text=False the dict. Needs the UETKEditor plugin (except for instances)."""
    obj = _asset(target)
    if isinstance(obj, unreal.MaterialInstance):
        spec = _instance_spec(obj)
    elif isinstance(obj, (unreal.Material, unreal.MaterialFunction)):
        spec = _Decompiler(obj).spec()
    else:
        raise MaterialError("%s is a %s, not a material, instance or material function" % (target, obj.get_class().get_name()))
    if not text:
        return spec
    return _spec_text(obj, spec)


def _spec_text(obj, spec):
    name = obj.get_name()
    lines = []
    if "instance_of" in spec:
        lines.append("%s: instance of %s" % (name, " <- ".join(spec["instance_of"])))
        lines.append("set:" if spec["set"] else "set: nothing (every parameter is the parent's)")
        lines += ["  %s = %s" % (k, json.dumps(v)) for k, v in sorted(spec["set"].items())]
        lines.append("params:")
        lines += ["  %s = %s" % (k, json.dumps(v)) for k, v in sorted(spec["params"].items())]
        return "\n".join(lines)
    if spec.get("function"):
        lines.append("%s: material function" % name)
        if spec.get("description"):
            lines.append("  description: %s" % spec["description"])
        lines.append("inputs:")
        lines += ["  %s: %s" % (k, json.dumps(v) if isinstance(v, dict) else v) for k, v in spec["inputs"].items()]
    else:
        lines.append("%s: %s, %s, %s%s" % (name, spec["domain"], spec["blend"], spec["shading"],
                                         ", two-sided" if spec.get("two_sided") else ""))
        if spec.get("usage"):
            lines.append("  usage: %s" % ", ".join(spec["usage"]))
        for k, v in (spec.get("properties") or {}).items():
            lines.append("  %s: %s" % (k, json.dumps(v)))
    if spec.get("params"):
        lines.append("params:")
        lines += ["  %s = %s" % (k, json.dumps(v)) for k, v in spec["params"].items()]
    if spec.get("let"):
        lines.append("let:")
        lines += ["  %s = %s" % kv for kv in spec["let"].items()]
    lines.append("out:")
    lines += ["  %s = %s" % kv for kv in spec["out"].items()]
    if spec.get("unused_nodes"):
        lines.append("unused nodes (connected to nothing): %s" % ", ".join(spec["unused_nodes"]))
    return "\n".join(lines)


# --------------------------------------------------------------------------- previews and discovery

_MESHES = {"sphere": "/Engine/BasicShapes/Sphere", "cube": "/Engine/BasicShapes/Cube",
           "plane": "/Engine/BasicShapes/Plane", "cylinder": "/Engine/BasicShapes/Cylinder",
           "cone": "/Engine/BasicShapes/Cone"}


def preview(target, mesh=None, out=None, **kwargs):
    """Render a material offscreen to PNG: surface materials on a mesh ('sphere' default, 'plane', 'cube',
    'cylinder', 'cone' or a static mesh path), post-process materials over a sphere and a cube. Other options
    go to ueh.render_asset (yaws, pitch, zoom, size, exposure, floor, environment)."""
    import ueh
    if mesh == "plane" and "pitch" not in kwargs:
        kwargs["pitch"] = -60.0
    return ueh.render_asset(target, out=out, mesh=_MESHES.get(mesh, mesh) if mesh else None, **kwargs)


def nodes(filter=None):
    """Material expression classes (for the AnyNode(...) syntax) with their input and output pins.
    filter: case-insensitive substring of the class name."""
    temp = unreal.new_object(unreal.Material)
    out = {}
    for attr in sorted(dir(unreal)):
        if not attr.startswith(PREFIX) or attr == PREFIX:
            continue
        name = attr[len(PREFIX):]
        if filter and filter.lower() not in name.lower():
            continue
        cls = getattr(unreal, attr)
        try:
            expr = MEL.create_material_expression(temp, cls, 0, 0)
        except Exception:
            expr = None
        if expr is None:
            continue
        out[name] = {"inputs": _pins(expr), "outputs": [o for o in _outs(expr)]}
    return out
