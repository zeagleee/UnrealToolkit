"""uprop - property plumbing shared by the toolkit's editor modules (mat, inputs): loose enum names, structs from
lists, asset paths, C++ or snake_case property names. Runs inside UnrealEditor."""
import json
import re

import unreal

EAL = unreal.EditorAssetLibrary


class PropError(RuntimeError):
    pass


_NAMES = {}


def norm(text):
    return re.sub(r"[^0-9a-z]", "", str(text).lower())


def snake(name):
    """C++ property name -> Python name: ConstA -> const_a, bUsedWithSkeletalMesh -> used_with_skeletal_mesh."""
    if len(name) > 1 and name[0] == "b" and name[1].isupper():
        name = name[1:]
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name).lower()


def object_path(text):
    """'/Game/T', '/Game/T.T' or "/Script/Engine.Texture2D'/Game/T.T'" -> '/Game/T.T'."""
    m = re.search(r"'([^']+)'", text)
    path = m.group(1) if m else text
    if "." not in path.rsplit("/", 1)[-1]:
        path = "%s.%s" % (path, path.rsplit("/", 1)[-1])
    return path


def short_path(path):
    """'/Game/T.T' -> '/Game/T' (when the object is named after its package)."""
    package, _, name = path.partition(".")
    return package if name == package.rsplit("/", 1)[-1] else path


def load(path):
    if isinstance(path, unreal.Object):
        return path
    obj = unreal.load_object(None, object_path(path))
    if obj is None:
        raise PropError("asset not found: %s" % path)
    return obj


def asset(target):
    """An asset by path ('/Game/A/B' or '/Game/A/B.B') or object. Works during PIE, where EditorAssetLibrary refuses."""
    if isinstance(target, unreal.Object):
        return target
    obj = unreal.load_object(None, object_path(target)) if target.startswith("/") else None
    if obj is None:
        raise PropError("asset not found: %s" % target)
    return obj


def exists(package):
    """Whether an asset package exists (asset registry; works during PIE)."""
    reg = unreal.AssetRegistryHelpers.get_asset_registry()
    return bool(reg.get_assets_by_package_name(unreal.Name(package.split(".")[0])))


def enum_value(enum_cls, value):
    """An enum member from a loose name: 'normal' -> SAMPLERTYPE_NORMAL, 'after_tonemapping' ->
    BL_SCENE_COLOR_AFTER_TONEMAPPING, 'PPI_PostProcessInput0' -> PPI_POST_PROCESS_INPUT0."""
    if isinstance(value, enum_cls):
        return value
    members = [m for m in dir(enum_cls) if m.isupper() and not m.startswith("_")]
    key = norm(value)
    singular = {key[:-2] if key.endswith("es") else None, key[:-1] if key.endswith("s") else None} - {None}
    for pick in (lambda m: norm(m) == key,
                 lambda m: "_" in m and norm(m.split("_", 1)[1]) == key,
                 lambda m: norm(m).endswith(key),
                 lambda m: "_" in m and norm(m.split("_", 1)[1]) in singular):  # bUsedWithSplineMeshes
        hits = [m for m in members if pick(m)]
        if len(hits) == 1:
            return getattr(enum_cls, hits[0])
    raise PropError("%r is not a %s: %s" % (value, enum_cls.__name__, ", ".join(members)))


def enum_name(value, strip_prefix=True):
    """SAMPLERTYPE_LINEAR_COLOR -> 'linear_color'."""
    name = value.name if hasattr(value, "name") else str(value).rsplit(".", 1)[-1]
    if strip_prefix and "_" in name:
        name = name.split("_", 1)[1]
    return name.lower()


def color(value, alpha=1.0):
    if isinstance(value, unreal.LinearColor):
        return value
    v = [float(x) for x in (value if isinstance(value, (list, tuple)) else [value] * 3)]
    if len(v) == 3:
        v.append(alpha)
    if len(v) != 4:
        raise PropError("a colour needs 3 or 4 numbers, got %r" % (value,))
    return unreal.LinearColor(*v)


def struct(current, values):
    """A struct of current's type from a list of numbers (Vector, Vector2D, Vector4f, LinearColor...)."""
    values = [float(v) for v in values]
    try:
        return type(current)(*values)
    except TypeError:
        out = type(current)()
        for fields in (("x", "y", "z", "w"), ("r", "g", "b", "a")):
            try:
                for name, v in zip(fields, values):
                    out.set_editor_property(name, v)
                return out
            except Exception:
                continue
        raise PropError("can't make a %s from %r" % (type(current).__name__, values))


def convert(current, value):
    """A spec value in the type the property already holds."""
    if current is not None and type(value) is type(current):
        return value
    if isinstance(current, unreal.EnumBase):
        return enum_value(type(current), value)
    if isinstance(current, bool):
        return bool(value)
    if isinstance(current, int):
        return int(value)
    if isinstance(current, float):
        return float(value)
    if isinstance(current, unreal.LinearColor):
        return color(value)
    if isinstance(value, (list, tuple)) and isinstance(current, unreal.StructBase):
        return struct(current, value)
    if isinstance(current, unreal.Name):
        return unreal.Name(str(value))
    if isinstance(current, unreal.Text):
        return unreal.Text(str(value))
    if current is None or isinstance(current, unreal.Object):
        return None if value in (None, "", "None") else load(value)
    if isinstance(current, str):
        return str(value)
    return value


def prop_name(obj, name):
    """The name get/set_editor_property accepts for `name` (C++ or snake_case), or None."""
    key = (obj.get_class().get_name(), name)
    if key not in _NAMES:
        found = None
        for candidate in (name, snake(name)):
            try:
                obj.get_editor_property(candidate)
                found = candidate
                break
            except Exception:
                pass
        _NAMES[key] = found
    return _NAMES[key]


def set_prop(obj, name, value):
    used = prop_name(obj, name)
    if used is None:
        raise PropError("%s has no property %r" % (obj.get_class().get_name(), name))
    current = obj.get_editor_property(used)
    if isinstance(value, str) and isinstance(current, (unreal.StructBase, unreal.Array)):
        lib = getattr(unreal, "UETKMaterialLibrary", None)
        if lib is None:
            raise PropError("setting %s from text needs the UETKEditor plugin" % name)
        result = json.loads(lib.set_property_text(obj, name, value))  # Unreal's text form, e.g. from describe()
        if not result.get("ok"):
            raise PropError(result.get("error"))
        return
    try:
        converted = convert(current, value)
        obj.set_editor_property(used, converted)
    except PropError:
        raise
    except Exception as exc:
        # Python refuses EditInstanceOnly properties on objects it takes for templates ("cannot be edited on
        # templates": a trigger's ChordAction before 5.8). The plugin's text import sets them anyway.
        lib = getattr(unreal, "UETKMaterialLibrary", None)
        if lib is not None and "template" in str(exc):
            text = (converted.get_path_name() if isinstance(converted, unreal.Object)
                    else converted.name if isinstance(converted, unreal.EnumBase) else str(converted))
            result = json.loads(lib.set_property_text(obj, name, text))
            if result.get("ok"):
                return
        raise PropError("%s.%s = %r: %s" % (obj.get_class().get_name(), used, value, exc))
