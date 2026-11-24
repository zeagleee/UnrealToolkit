"""inputs - Enhanced Input assets from Python: actions, mapping contexts, triggers and modifiers. Runs inside
UnrealEditor; pre-imported as `inputs` in `ue.py py`.

    inputs.build(SPEC)                                   # create or update actions and contexts, then save
    inputs.describe()                                    # every mapping context and action in /Game, as a SPEC
    inputs.describe('/Game/Input/IMC_Default')           # one context (or action)
    inputs.action('/Game/Input/IA_Sprint', 'bool')       # one action (type, triggers, modifiers, description)
    inputs.map_key('/Game/Input/IMC_Default', 'IA_Sprint', 'LeftShift', triggers=['hold'])
    inputs.unmap('/Game/Input/IMC_Default', action='IA_Sprint')       # or key='LeftShift', or both
    inputs.use_context('/Game/BP_PlayerController', '/Game/Input/IMC_Default')  # a Blueprint's default contexts
    inputs.add_context('/Game/Input/IMC_Vehicle', priority=1)          # in the running game (PIE / --instance)
    inputs.remove_context(...), inputs.active()          # which contexts the player has now
    inputs.keys('gamepad')                               # key names

SPEC (dict or JSON text):
    {'actions': {'/Game/Input/IA_Sprint': {'type': 'bool', 'triggers': ['down'], 'description': 'Run while held',
                                          'mappable': {'name': 'Sprint', 'display_name': 'Sprint',
                                                       'category': 'Movement'}},
                 '/Game/Input/IA_Move': 'axis2d'},
     'contexts': {'/Game/Input/IMC_Default': {
                     'mappings': {'IA_Sprint': ['LeftShift', 'Gamepad_LeftThumbstick'],
                                  'IA_Move': [{'key': 'W', 'modifiers': ['swizzle']},
                                              {'key': 'S', 'modifiers': ['swizzle', 'negate']}]},
                     'description': 'On foot', 'replace': False}}}
    An action listed under a context gets exactly those keys there; the context's other actions keep theirs unless
    'replace' is true. Actions are named by path, or by asset name when one InputAction (in the spec or the project)
    has it. Building an existing asset updates it in place, so references stay valid.
    Value types: bool, axis1d, axis2d, axis3d.
    Triggers: down pressed released hold hold_and_release tap pulse chord combo (or any InputTrigger class).
    Modifiers: negate swizzle dead_zone scalar response_curve smooth smooth_delta fov_scaling to_world_space
        scale_by_delta_time (or any InputModifier class).
    Each trigger or modifier is a name, or {name: {property: value}}: {'hold': {'hold_time_threshold': 0.5}},
        {'chord': {'chord_action': 'IA_Aim'}}, {'swizzle': {'order': 'zyx'}}, {'negate': {'x': False}},
        {'dead_zone': {'lower_threshold': 0.25}}.
    Keys are key names: W, SpaceBar, LeftShift, LeftMouseButton, Mouse2D, Gamepad_FaceButton_Bottom, Gamepad_Left2D,
        Gamepad_LeftThumbstick (the stick's button)... inputs.keys() lists them.
    Mappings in a context's per-profile overrides (5.7+) aren't read or written yet.
"""
import difflib
import json
import re

import unreal

import uprop
from uprop import PropError

EAL = unreal.EditorAssetLibrary


class InputError(PropError):
    pass


_TYPES = {"bool": "BOOLEAN", "boolean": "BOOLEAN", "digital": "BOOLEAN", "axis1d": "AXIS1D", "float": "AXIS1D",
          "axis2d": "AXIS2D", "vector2d": "AXIS2D", "vector2": "AXIS2D", "axis3d": "AXIS3D", "vector": "AXIS3D"}
_TYPE_NAMES = {"BOOLEAN": "bool", "AXIS1D": "axis1d", "AXIS2D": "axis2d", "AXIS3D": "axis3d"}
_TRIGGERS = {"down": "InputTriggerDown", "pressed": "InputTriggerPressed", "released": "InputTriggerReleased",
             "hold": "InputTriggerHold", "hold_and_release": "InputTriggerHoldAndRelease", "tap": "InputTriggerTap",
             "pulse": "InputTriggerPulse", "chord": "InputTriggerChordAction", "combo": "InputTriggerCombo"}
_MODIFIERS = {"negate": "InputModifierNegate", "swizzle": "InputModifierSwizzleAxis",
              "dead_zone": "InputModifierDeadZone", "scalar": "InputModifierScalar", "scale": "InputModifierScalar",
              "response_curve": "InputModifierResponseCurveExponential", "smooth": "InputModifierSmooth",
              "smooth_delta": "InputModifierSmoothDelta", "fov_scaling": "InputModifierFOVScaling",
              "to_world_space": "InputModifierToWorldSpace", "scale_by_delta_time": "InputModifierScaleByDeltaTime"}
_SHORT = {cls: name for name, cls in list(_TRIGGERS.items()) + list(_MODIFIERS.items()) if name != "scale"}
# Engine bookkeeping on triggers and modifiers, not settings.
_SKIP_PROPS = {"last_value", "held_duration", "trigger_when_paused"}


# --------------------------------------------------------------------------- assets

def _registry_assets(cls_name):
    reg = unreal.AssetRegistryHelpers.get_asset_registry()
    path = unreal.TopLevelAssetPath("/Script/EnhancedInput", cls_name)
    return sorted(str(d.package_name) for d in reg.get_assets_by_class(path, True)
                  if str(d.package_name).startswith("/Game"))


def _existing(path, cls):
    package = path.split(".")[0]
    if not uprop.exists(package):
        return None
    obj = uprop.asset(package)
    if not isinstance(obj, cls):
        raise InputError("%s already exists and is a %s, not a %s" % (package, obj.get_class().get_name(), cls.__name__))
    return obj


def _create(path, cls, factory):
    package = path.split(".")[0]
    if not package.startswith("/"):
        raise InputError("give a full asset path for a new asset, e.g. /Game/Input/%s" % package)
    folder, name = package.rsplit("/", 1)
    obj = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, cls, factory)
    if obj is None:
        raise InputError("could not create %s" % package)
    return obj


def _save(obj):
    return bool(unreal.EditorLoadingAndSavingUtils.save_packages([obj.get_outermost()], False))


def _find_action(ref, known=None):
    """An InputAction from a path, an object, or an asset name (spec actions first, then the project's)."""
    if isinstance(ref, unreal.InputAction):
        return ref
    if not isinstance(ref, str):
        raise InputError("an action is a path or a name, got %r" % (ref,))
    if ref.startswith("/"):
        obj = _existing(ref, unreal.InputAction)
        if obj is None:
            raise InputError("no InputAction at %s" % ref)
        return obj
    for path, obj in (known or {}).items():
        if path.rsplit("/", 1)[-1].split(".")[0] == ref:
            return obj
    hits = [p for p in _registry_assets("InputAction") if p.rsplit("/", 1)[-1] == ref]
    if len(hits) == 1:
        return uprop.asset(hits[0])
    if hits:
        raise InputError("several InputActions are named %s: %s; give the path" % (ref, ", ".join(hits)))
    names = sorted({p.rsplit("/", 1)[-1] for p in _registry_assets("InputAction")})
    close = difflib.get_close_matches(ref, names, 3)
    raise InputError("no InputAction named %s%s" % (ref, "; did you mean " + ", ".join(close) if close else ""))


def _find_context(ref):
    if isinstance(ref, unreal.InputMappingContext):
        return ref
    if not ref.startswith("/"):
        hits = [p for p in _registry_assets("InputMappingContext") if p.rsplit("/", 1)[-1] == ref]
        if len(hits) != 1:
            raise InputError("%s InputMappingContext named %s%s" % ("no" if not hits else "several", ref,
                                                                    ": " + ", ".join(hits) if hits else ""))
        ref = hits[0]
    obj = _existing(ref, unreal.InputMappingContext)
    if obj is None:
        raise InputError("no InputMappingContext at %s" % ref)
    return obj


# --------------------------------------------------------------------------- keys

_KEY_CACHE = []


def keys(filter=None):
    """Key names (for mappings and ueh.press): filter is a case-insensitive substring ('gamepad', 'mouse')."""
    if not _KEY_CACHE:
        found = set()
        lib = getattr(unreal, "UETKEditorLibrary", None)  # Python has no list of keys; the plugin reads EKeys
        if lib is not None and hasattr(lib, "list_keys"):
            found.update(str(k) for k in lib.list_keys())
        _KEY_CACHE.extend(sorted(found))
    names = list(_KEY_CACHE)
    return [n for n in names if not filter or filter.lower() in n.lower()]


def _key(name):
    if isinstance(name, unreal.Key):
        return name
    k = unreal.Key()
    k.set_editor_property("key_name", unreal.Name(str(name)))
    if not unreal.InputLibrary.key_is_valid(k):
        close = difflib.get_close_matches(str(name), keys(), 4) or difflib.get_close_matches(
            str(name).lower(), [n.lower() for n in keys()], 4)
        raise InputError("no key named %r%s (inputs.keys() lists them)" % (name, "; did you mean " + ", ".join(close)
                                                                         if close else ""))
    return k


def _key_name(k):
    return str(k.get_editor_property("key_name"))


# --------------------------------------------------------------------------- triggers and modifiers

def _class(kind, name):
    table = _TRIGGERS if kind == "trigger" else _MODIFIERS
    base = "InputTrigger" if kind == "trigger" else "InputModifier"
    cls_name = table.get(name) or table.get(uprop.snake(name)) or name
    if not cls_name.startswith(base):
        cls_name = base + "".join(p[:1].upper() + p[1:] for p in name.split("_"))
    cls = getattr(unreal, cls_name, None)
    if cls is None:
        known = sorted(table)
        close = difflib.get_close_matches(name, known, 3)
        raise InputError("no %s %r%s (%s)" % (kind, name, "; did you mean " + ", ".join(close) if close else "",
                                              ", ".join(known)))
    return cls


def _instance(kind, item, outer, known):
    """A trigger or modifier object (outered to the asset that holds it) from a spec item."""
    if isinstance(item, dict):
        if len(item) != 1:
            raise InputError("a %s is a name or {name: {properties}}, got %r" % (kind, item))
        name, props = next(iter(item.items()))
    else:
        name, props = item, {}
    obj = unreal.new_object(_class(kind, name), outer)
    for key, value in (props or {}).items():
        used = uprop.prop_name(obj, key)
        if used is None:
            raise InputError("%s %s has no property %r" % (kind, name, key))
        current = obj.get_editor_property(used)
        if isinstance(value, str) and not value.startswith("/") and "action" in used.lower() and \
                (current is None or isinstance(current, unreal.InputAction)):
            value = _find_action(value, known)
        uprop.set_prop(obj, used, value)
    return obj


def _plain(value):
    if isinstance(value, unreal.EnumBase):
        return value.name.lower()
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        return float(format(value, ".6g"))
    if isinstance(value, unreal.Object):
        return uprop.short_path(value.get_path_name())
    if isinstance(value, (unreal.Name, unreal.Text)):
        return str(value)
    if isinstance(value, (list, tuple, unreal.Array)):
        return [_plain(v) for v in value]
    if isinstance(value, unreal.StructBase):
        for fields in ("xyzw", "rgba"):
            parts = []
            for f in fields:
                try:
                    parts.append(_plain(value.get_editor_property(f)))
                except Exception:
                    break
            if len(parts) >= 2:
                return parts
    return str(value)


def _nondefault(obj):
    """{property: value} of an object's settings that differ from its class defaults."""
    import ueh
    cdo = unreal.get_default_object(obj.get_class())
    out = {}
    for name, (_type, access, _doc) in sorted(ueh.prop_specs(obj).items()):
        if access != "Read-Write" or name in _SKIP_PROPS:
            continue
        try:
            a, b = obj.get_editor_property(name), cdo.get_editor_property(name)
        except Exception:
            continue
        if _plain(a) != _plain(b):
            out[name] = _plain(a)
    return out


def _item_spec(obj):
    cls = obj.get_class().get_name()
    name = _SHORT.get(cls, cls)
    props = _nondefault(obj)
    return {name: props} if props else name


# --------------------------------------------------------------------------- mappings

def _mappings(imc):
    """The context's default mappings (5.7+: DefaultKeyMappings.Mappings; 5.6: Mappings), as a list of copies."""
    try:
        data = imc.get_editor_property("default_key_mappings")
        return list(data.get_editor_property("mappings"))
    except Exception:
        return list(imc.get_editor_property("mappings"))


def _set_mappings(imc, mappings):
    try:
        data = imc.get_editor_property("default_key_mappings")
    except Exception:
        imc.set_editor_property("mappings", mappings)
        return
    data.set_editor_property("mappings", mappings)
    imc.set_editor_property("default_key_mappings", data)


def _mapping(imc, action, item, known):
    """An EnhancedActionKeyMapping from a key name or {'key', 'triggers', 'modifiers'}."""
    spec = {"key": item} if isinstance(item, str) else dict(item)
    if "key" not in spec:
        raise InputError("a mapping needs a key, got %r" % (item,))
    m = unreal.EnhancedActionKeyMapping()
    m.set_editor_property("action", action)
    m.set_editor_property("key", _key(spec["key"]))
    m.set_editor_property("triggers", [_instance("trigger", t, imc, known) for t in spec.get("triggers") or []])
    m.set_editor_property("modifiers", [_instance("modifier", t, imc, known) for t in spec.get("modifiers") or []])
    for key, value in spec.items():
        if key not in ("key", "triggers", "modifiers"):
            uprop.set_prop(m, key, value)
    return m


def _mapping_spec(m):
    triggers = [_item_spec(t) for t in m.get_editor_property("triggers") if t]
    modifiers = [_item_spec(t) for t in m.get_editor_property("modifiers") if t]
    key = _key_name(m.get_editor_property("key"))
    if not triggers and not modifiers:
        return key
    spec = {"key": key}
    if triggers:
        spec["triggers"] = triggers
    if modifiers:
        spec["modifiers"] = modifiers
    return spec


def map_key(context, action, key, triggers=None, modifiers=None, save_asset=True):
    """Add one mapping to a context (keeps the others): map_key('/Game/Input/IMC_Default', 'IA_Sprint', 'LeftShift')."""
    imc, ia = _find_context(context), _find_action(action)
    mappings = _mappings(imc)
    mappings.append(_mapping(imc, ia, {"key": key, "triggers": triggers or [], "modifiers": modifiers or []}, {}))
    _set_mappings(imc, mappings)
    if save_asset:
        _save(imc)
    return describe(imc, text=False)


def unmap(context, action=None, key=None, save_asset=True):
    """Remove a context's mappings of an action, of a key, or of that action on that key. Returns how many went."""
    imc = _find_context(context)
    ia = _find_action(action) if action else None
    k = _key_name(_key(key)) if key else None
    if ia is None and k is None:
        raise InputError("give action=, key= or both (to clear a context, build it with 'replace': True)")
    keep = [m for m in _mappings(imc)
            if not ((ia is None or m.get_editor_property("action") == ia)
                    and (k is None or _key_name(m.get_editor_property("key")) == k))]
    removed = len(_mappings(imc)) - len(keep)
    _set_mappings(imc, keep)
    if save_asset:
        _save(imc)
    return removed


# --------------------------------------------------------------------------- actions

def action(path, type=None, triggers=None, modifiers=None, description=None, mappable=None, save_asset=True,
           known=None, **props):
    """Create or update an InputAction. type: bool/axis1d/axis2d/axis3d; triggers/modifiers: spec items (module
    doc); mappable: {'name', 'display_name', 'category'} makes it a player-mappable (rebindable) key setting;
    other keywords set properties (consume_input, trigger_when_paused, accumulation_behavior...).
    Triggers and modifiers given replace the action's own; None leaves them."""
    ia = _existing(path, unreal.InputAction) or _create(path, unreal.InputAction, unreal.InputAction_Factory())
    known = dict(known or {})
    known[path] = ia
    if type is not None:
        value = _TYPES.get(str(type).lower())
        if value is None:
            raise InputError("action type %r is not one of bool, axis1d, axis2d, axis3d" % type)
        ia.set_editor_property("value_type", getattr(unreal.InputActionValueType, value))
    if triggers is not None:
        ia.set_editor_property("triggers", [_instance("trigger", t, ia, known) for t in triggers])
    if modifiers is not None:
        ia.set_editor_property("modifiers", [_instance("modifier", t, ia, known) for t in modifiers])
    if description is not None:
        ia.set_editor_property("action_description", unreal.Text(description))
    if mappable is not None:
        if mappable is False:
            ia.set_editor_property("player_mappable_key_settings", None)
        else:
            settings = unreal.new_object(unreal.PlayerMappableKeySettings, ia)
            info = dict(mappable) if isinstance(mappable, dict) else {}
            name = info.get("name") or path.rsplit("/", 1)[-1]
            settings.set_editor_property("name", unreal.Name(name))
            settings.set_editor_property("display_name", unreal.Text(info.get("display_name", name)))
            if info.get("category"):
                settings.set_editor_property("display_category", unreal.Text(info["category"]))
            ia.set_editor_property("player_mappable_key_settings", settings)
    for key, value in props.items():
        uprop.set_prop(ia, key, value)
    if save_asset:
        _save(ia)
    return ia


def _action_spec(ia):
    spec = {"type": _TYPE_NAMES.get(ia.get_editor_property("value_type").name, "bool")}
    triggers = [_item_spec(t) for t in ia.get_editor_property("triggers") if t]
    modifiers = [_item_spec(t) for t in ia.get_editor_property("modifiers") if t]
    if triggers:
        spec["triggers"] = triggers
    if modifiers:
        spec["modifiers"] = modifiers
    desc = str(ia.get_editor_property("action_description"))
    if desc:
        spec["description"] = desc
    settings = ia.get_editor_property("player_mappable_key_settings")
    if settings:
        spec["mappable"] = {"name": str(settings.get_editor_property("name")),
                            "display_name": str(settings.get_editor_property("display_name"))}
        category = str(settings.get_editor_property("display_category"))
        if category:
            spec["mappable"]["category"] = category
    cdo = unreal.get_default_object(unreal.InputAction)
    for name in ("consume_input", "trigger_when_paused", "accumulation_behavior", "consumes_action_and_axis_mappings",
                 "reserve_all_mappings"):
        try:
            value = ia.get_editor_property(name)
        except Exception:
            continue
        if _plain(value) != _plain(cdo.get_editor_property(name)):
            spec[name] = _plain(value)
    return spec if len(spec) > 1 else spec["type"]


# --------------------------------------------------------------------------- build and describe

def build(spec, save_asset=True):
    """Create or update the actions and contexts of a spec (module doc). Returns what changed."""
    if isinstance(spec, str):
        spec = json.loads(spec)
    known, result = {}, {"actions": [], "contexts": []}
    for path, info in (spec.get("actions") or {}).items():
        info = {"type": info} if isinstance(info, str) else dict(info)
        known[path] = action(path, save_asset=False, known=known, **info)
        result["actions"].append(path)
    for path, info in (spec.get("contexts") or {}).items():
        info = dict(info)
        table = info.pop("mappings") if "mappings" in info else \
            {k: info.pop(k) for k in list(info) if k not in ("description", "replace")}
        imc = _existing(path, unreal.InputMappingContext) or \
            _create(path, unreal.InputMappingContext, unreal.InputMappingContext_Factory())
        actions = {name: _find_action(name, known) for name in table}
        listed = set(actions.values())
        mappings = [] if info.get("replace") else \
            [m for m in _mappings(imc) if m.get_editor_property("action") not in listed]
        for name, items in table.items():
            for item in ([items] if isinstance(items, (str, dict)) else items):
                mappings.append(_mapping(imc, actions[name], item, known))
        _set_mappings(imc, mappings)
        if "description" in info:
            imc.set_editor_property("context_description", unreal.Text(info["description"]))
        known[path] = imc
        result["contexts"].append(path)
    if save_asset:
        result["saved"] = all(_save(obj) for obj in known.values())
    return result


def _action_ref(ia):
    """An action's asset name when no other InputAction has it, else its path (what build() accepts)."""
    if ia is None:
        return "None"
    name = ia.get_name()
    same = [p for p in _registry_assets("InputAction") if p.rsplit("/", 1)[-1] == name]
    return name if len(same) <= 1 else ia.get_path_name().split(".")[0]


def _context_spec(imc):
    table = {}
    for m in _mappings(imc):
        name = _action_ref(m.get_editor_property("action"))
        table.setdefault(name, []).append(_mapping_spec(m))
    spec = {"mappings": table}
    desc = str(imc.get_editor_property("context_description"))
    if desc:
        spec["description"] = desc
    return spec


def _used_by(path):
    reg = unreal.AssetRegistryHelpers.get_asset_registry()
    try:
        refs = reg.get_referencers(unreal.Name(path.split(".")[0]), unreal.AssetRegistryDependencyOptions())
    except Exception:
        return []
    return sorted(str(r) for r in refs or [])


def describe(target=None, text=True, root="/Game"):
    """The project's input (every context and action under root), or one context or action, as a SPEC that build()
    takes back. text=True returns readable text with where each context is used; False the dict."""
    contexts, actions = {}, {}
    if target is None:
        for path in _registry_assets("InputMappingContext"):
            if path.startswith(root):
                contexts[path] = uprop.asset(path)
        for path in _registry_assets("InputAction"):
            if path.startswith(root):
                actions[path] = uprop.asset(path)
    else:
        obj = target if isinstance(target, unreal.Object) else uprop.asset(target)
        path = obj.get_path_name().split(".")[0]
        if isinstance(obj, unreal.InputMappingContext):
            contexts[path] = obj
            for m in _mappings(obj):
                ia = m.get_editor_property("action")
                if ia:
                    actions[ia.get_path_name().split(".")[0]] = ia
        elif isinstance(obj, unreal.InputAction):
            actions[path] = obj
        else:
            raise InputError("%s is a %s, not an InputMappingContext or InputAction" % (path, obj.get_class().get_name()))
    spec = {"actions": {p: _action_spec(a) for p, a in sorted(actions.items())},
            "contexts": {p: _context_spec(c) for p, c in sorted(contexts.items())}}
    if not text:
        return spec
    return _text(spec, target is None)


def _item_text(item):
    if isinstance(item, str):
        return item
    name, props = next(iter(item.items()))
    return "%s(%s)" % (name, ", ".join("%s=%s" % (k, json.dumps(v)) for k, v in props.items()))


def _text(spec, whole_project):
    lines, mapped = [], set()
    for path, ctx in spec["contexts"].items():
        users = [u for u in _used_by(path) if u != path]
        lines.append("%s%s" % (path, ("  (used by %s)" % ", ".join(users)) if users else "  (used by nothing)"))
        if ctx.get("description"):
            lines.append("  description: %s" % ctx["description"])
        for name, items in ctx["mappings"].items():
            mapped.add(name)
            parts = []
            for item in items:
                if isinstance(item, str):
                    parts.append(item)
                else:
                    extra = [_item_text(t) for t in item.get("triggers", [])] + \
                            [_item_text(t) for t in item.get("modifiers", [])]
                    parts.append("%s [%s]" % (item["key"], ", ".join(extra)))
            lines.append("  %-18s %s" % (name, ", ".join(parts)))
    lines.append("actions:")
    for path, a in spec["actions"].items():
        name = path.rsplit("/", 1)[-1]
        if name not in mapped and path in mapped:
            name = path
        if isinstance(a, str):
            detail = a
        else:
            bits = [a["type"]]
            bits += ["triggers: " + ", ".join(_item_text(t) for t in a.get("triggers", []))] if a.get("triggers") else []
            bits += ["modifiers: " + ", ".join(_item_text(t) for t in a.get("modifiers", []))] if a.get("modifiers") else []
            bits += ["%s: %s" % (k, json.dumps(v)) for k, v in a.items()
                     if k not in ("type", "triggers", "modifiers")]
            detail = "; ".join(bits)
        unmapped = "" if (name in mapped or not whole_project) else "  (in no context)"
        lines.append("  %-18s %s%s" % (name, detail, unmapped))
    return "\n".join(lines)


# --------------------------------------------------------------------------- Blueprints and the running game

def use_context(blueprint, context, prop=None, save_asset=True, remove=False):
    """Put a mapping context in a Blueprint's input-context property (e.g. a player controller's
    DefaultMappingContexts): appended to an array, or set. prop: the property name when there are several.
    remove=True takes it out again."""
    import ueh
    imc = _find_context(context)
    cdo = ueh.resolve(blueprint)
    specs = ueh.prop_specs(cdo)
    candidates = [n for n, (t, access, _) in specs.items() if "InputMappingContext" in t and access == "Read-Write"]
    if prop is None:
        if len(candidates) != 1:
            default = [c for c in candidates if "default" in c]
            if len(default) != 1:
                raise InputError("%s has %s; give prop=" % (
                    blueprint, "these context properties: " + ", ".join(candidates) if candidates
                    else "no InputMappingContext property (set it in its C++ class, or a variable)"))
            candidates = default
        prop = candidates[0]
    used = uprop.prop_name(cdo, prop)
    if used is None:
        raise InputError("%s has no property %r (context properties: %s)" % (blueprint, prop, ", ".join(candidates)))
    current = cdo.get_editor_property(used)
    if isinstance(current, unreal.Array) or isinstance(current, list):
        values = [v for v in current if v != imc] if remove else list(current) + ([] if imc in current else [imc])
        cdo.set_editor_property(used, values)
    else:
        cdo.set_editor_property(used, None if remove else imc)
    bp = uprop.asset(blueprint)
    unreal.BlueprintEditorLibrary.compile_blueprint(bp)
    if save_asset:
        _save(bp)
    return {"blueprint": bp.get_path_name().split(".")[0], "property": used,
            "value": _plain(cdo.get_editor_property(used))}


def _subsystem(player=0):
    import ueh
    world = ueh.game_world()
    if world is None:
        raise InputError("no game running (start PIE, or use --instance with a ue.py net process)")
    subs = [s for s in unreal.ObjectIterator(unreal.EnhancedInputLocalPlayerSubsystem) if s.get_world() == world]
    if len(subs) <= player:
        raise InputError("no local player %d in the running game (%d found)" % (player, len(subs)))
    return subs[player]


def add_context(context, priority=0, player=0):
    """Add a mapping context to a local player in the running game (PIE or a ue.py net process)."""
    sub = _subsystem(player)
    sub.add_mapping_context(_find_context(context), int(priority), unreal.ModifyContextOptions())
    return active(player)


def remove_context(context, player=0):
    sub = _subsystem(player)
    sub.remove_mapping_context(_find_context(context), unreal.ModifyContextOptions())
    return active(player)


def active(player=0):
    """The project's mapping contexts a local player has right now: {path: priority}."""
    sub = _subsystem(player)
    out = {}
    for path in _registry_assets("InputMappingContext"):
        # Python returns the out-parameter instead of the bool: the priority when applied, None when not.
        priority = sub.has_mapping_context(uprop.asset(path))
        if isinstance(priority, tuple):
            priority = priority[1] if priority[0] else None
        if priority is not None:
            out[path] = priority
    return out
