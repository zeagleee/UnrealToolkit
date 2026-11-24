"""ui - UMG menus and HUDs: read and drive the widgets on screen in a running game, and build Widget Blueprints from a
spec. Runs inside UnrealEditor or a ue.py net game process; pre-imported as `ui` in `ue.py py`. Needs the UETKEditor
plugin (Python can't reach a widget tree's root).

In the running game (PIE, or `ue.py --instance client1 ...`):
    print(ui.tree())                    # every widget on screen: class, name, text, enabled, rectangle in pixels
    ui.click('Resume')                  # a button (or checkbox) by its label or name, as a real click fires it
    ui.type('PlayerName', 'Ada')        # an editable text: set, changed, committed (Enter)
    ui.set('Volume', 0.3)               # slider, spin box, checkbox, combo box
    ui.text('Status'), ui.text()        # one widget's text, or all text on screen
    ui.find('Resume')                   # the widget object
    ui.open('/Game/UI/WBP_Inventory')   # put a widget on screen (Create Widget + Add to Viewport)
    yield ui.wait('Paused')             # in a scenario: until it's on screen (gone=True: until it isn't)
Targets match a widget's name, or its label (its text, or the text inside it, ignoring case): exactly first, then
the start of the label, then anywhere in it. A button and the text inside it share a label; the button wins. Only
widgets on screen count unless hidden=True. player=N picks a local player's widgets.
Rectangles (tree()) need the game view to have been painted: an editor in the background doesn't paint its PIE
viewport, so they're left out there.
Clicks, typing and values go through the widgets' own events (OnPressed, OnReleased, OnClicked; OnTextChanged,
OnTextCommitted; OnValueChanged...), so Blueprint and C++ handlers run as for a real click. They don't go through
Slate's hit testing: a button covered by another widget still gets clicked.

Building Widget Blueprints (the designer tree):
    ui.build(SPEC, '/Game/UI/WBP_PauseMenu')   # create, or rebuild the tree in place; compiles and saves
    print(ui.describe('/Game/UI/WBP_PauseMenu'))
    g = bpedit.graph('/Game/UI/WBP_PauseMenu'); g.bound_event('ResumeButton', 'OnClicked')   # its event graph
SPEC: {'parent': 'UserWidget',            # the parent class (only used when creating)
       'root': {'type': 'CanvasPanel', 'name': 'Root', 'children': [
           {'type': 'VerticalBox', 'name': 'Menu',
            'slot': {'anchors': [0.5, 0.5, 0.5, 0.5], 'alignment': [0.5, 0.5], 'auto_size': True},
            'children': [
                {'type': 'TextBlock', 'name': 'Title', 'text': 'Paused', 'font_size': 36},
                {'type': 'Button', 'name': 'ResumeButton', 'text': 'Resume', 'slot': {'padding': 8}},
                {'type': 'CheckBox', 'name': 'InvertY', 'props': {'checked_state': 'checked'}}]}]}}
    type: any widget class (TextBlock, Button, Image, Border, SizeBox, CanvasPanel, VerticalBox, HorizontalBox,
          Overlay, ScrollBox, CheckBox, Slider, ProgressBar, EditableTextBox, ComboBoxString, SpinBox, Spacer...)
          or a Widget Blueprint path. Every widget is a variable, so the graph can use it.
    text: a TextBlock's text; on a Button, a TextBlock inside it. font_size, color ([r, g, b, a] linear) for text.
    props: any property (snake_case or C++ name). slot: canvas slots take position, size, anchors ([min x, min y,
          max x, max y]), alignment, auto_size, z_order; box and overlay slots take padding (a number or [l, t, r, b]),
          h_align / v_align (left|center|right|fill, top|center|bottom|fill), fill (a weight; omit for auto size).
"""
import json
import re

import unreal

import uprop
from uprop import PropError

EAL = unreal.EditorAssetLibrary


class UIError(PropError):
    pass


def _lib():
    lib = getattr(unreal, "UETKWidgetLibrary", None)
    if lib is None:
        raise UIError("needs the UETKEditor plugin: `ue.py plugins install`, then `ue.py build --restart`")
    return lib


def _norm_label(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


# --------------------------------------------------------------------------- reading widgets

def _own_text(w):
    """The text a widget shows itself (not its children's), or None."""
    try:
        if isinstance(w, (unreal.TextBlock, unreal.EditableText, unreal.EditableTextBox,
                          unreal.MultiLineEditableText, unreal.MultiLineEditableTextBox)):
            return str(w.get_text())
        if isinstance(w, unreal.RichTextBlock):
            return str(w.get_text())
        if isinstance(w, unreal.ComboBoxString):
            return str(w.get_selected_option())
    except Exception:
        return None
    return None


def _value(w):
    """A control's value: checkbox state, slider or spin box value, progress percent."""
    try:
        if isinstance(w, unreal.CheckBox):
            return bool(w.is_checked())
        if isinstance(w, (unreal.Slider, unreal.SpinBox)):
            return round(float(w.get_value()), 4)
        if isinstance(w, unreal.ProgressBar):
            return round(float(w.get_editor_property("percent")), 4)
    except Exception:
        return None
    return None


def _children(w):
    if isinstance(w, unreal.UserWidget):
        root = _lib().get_root_widget(w)
        return [root] if root else []
    if isinstance(w, unreal.PanelWidget):  # ContentWidget (Button, Border, SizeBox...) is a PanelWidget too
        out = []
        for i in range(w.get_children_count()):
            child = w.get_child_at(i)
            if child is not None:
                out.append(child)
        return out
    return []


_SHOWN = None


def _shown(w):
    global _SHOWN
    if _SHOWN is None:
        _SHOWN = {unreal.SlateVisibility.COLLAPSED, unreal.SlateVisibility.HIDDEN}
    try:
        return w.get_visibility() not in _SHOWN
    except Exception:
        return True


def _rect(w, world):
    try:
        geometry = w.get_cached_geometry()
        size = unreal.SlateLibrary.get_local_size(geometry)
        if size.x <= 0 and size.y <= 0:
            return None
        pixel, _viewport = unreal.SlateLibrary.local_to_viewport(world, geometry, unreal.Vector2D(0, 0))
        far, _ = unreal.SlateLibrary.local_to_viewport(world, geometry, size)
        return [round(pixel.x), round(pixel.y), round(far.x - pixel.x), round(far.y - pixel.y)]
    except Exception:
        return None


def _world():
    import ueh
    world = ueh.game_world()
    if world is None:
        raise UIError("no game running (start PIE, or use --instance with a ue.py net process)")
    return world


def _top_level(world, player=None):
    found = unreal.WidgetLibrary.get_all_widgets_of_class(world, unreal.UserWidget, True)
    widgets = [w for w in found if w is not None and w.is_in_viewport()]
    if player is not None:
        pc = unreal.GameplayStatics.get_player_controller(world, int(player))
        widgets = [w for w in widgets if w.get_owning_player() == pc]
    return widgets


def _walk(world, player=None, hidden=False):
    """Every widget on screen as a list of nodes: {w, parent (index), depth, shown, name, cls, text, label}."""
    nodes = []

    def visit(w, parent, depth, shown):
        shown = shown and _shown(w)
        if not shown and not hidden:
            return
        node = {"w": w, "parent": parent, "depth": depth, "shown": shown, "name": w.get_name(),
                "cls": w.get_class().get_name(), "text": _own_text(w), "value": _value(w)}
        nodes.append(node)
        index = len(nodes) - 1
        kids = _children(w)
        if isinstance(w, unreal.WidgetSwitcher):  # only the active page is on screen
            active = w.get_active_widget()
            kids = [k for k in kids if k == active] if not hidden else kids
        for child in kids:
            visit(child, index, depth + 1, shown)

    for top in _top_level(world, player):
        visit(top, None, 0, True)
    # labels: own text, else the text of the widgets inside
    for i in range(len(nodes) - 1, -1, -1):
        n = nodes[i]
        inner = [m["label"] for m in nodes[i + 1:] if m["parent"] == i and m.get("label")]
        n["label"] = n["text"] if n["text"] else " ".join(inner)
    return nodes


def tree(player=None, hidden=False, text=True):
    """The widgets on screen as an indented outline (text=False: a list of dicts): class, name, text or value,
    disabled, and the rectangle in viewport pixels [x, y, width, height]."""
    world = _world()
    nodes = _walk(world, player, hidden)
    if not text:
        return [{"name": n["name"], "class": n["cls"], "depth": n["depth"], "shown": n["shown"], "text": n["text"],
                 "value": n["value"], "enabled": bool(n["w"].get_is_enabled()), "rect": _rect(n["w"], world)}
                for n in nodes]
    if not nodes:
        return "(no widgets on screen)"
    lines = []
    for n in nodes:
        bits = ["%s%s (%s)" % ("  " * n["depth"], n["name"], n["cls"])]
        if n["text"]:
            bits.append(json.dumps(n["text"]))
        if n["value"] is not None:
            bits.append("= %s" % json.dumps(n["value"]))
        if not n["w"].get_is_enabled():
            bits.append("disabled")
        if not n["shown"]:
            bits.append("hidden")
        r = _rect(n["w"], world)
        if r:
            bits.append("[%d,%d %dx%d]" % tuple(r))
        lines.append("  ".join(bits))
    return "\n".join(lines)


_KINDS = {
    "click": (unreal.Button, unreal.CheckBox),
    "type": (unreal.EditableText, unreal.EditableTextBox, unreal.MultiLineEditableText, unreal.MultiLineEditableTextBox),
    "set": (unreal.Slider, unreal.SpinBox, unreal.CheckBox, unreal.ComboBoxString),
}


_CONTROLS = _KINDS["click"] + _KINDS["type"] + _KINDS["set"]


def _resolve(nodes, target, kinds=None):
    """The node a target names: by widget name, then by label (exact, start, anywhere); a match inside a widget of
    the wanted kind (the text inside a button) resolves to that widget."""
    want = _norm_label(target)

    def lift(i):
        if not kinds:
            return i
        j = i
        while j is not None:
            if isinstance(nodes[j]["w"], kinds):
                return j
            j = nodes[j]["parent"]
        return None

    tiers = [
        [i for i, n in enumerate(nodes) if n["name"].lower() == want],
        [i for i, n in enumerate(nodes) if _norm_label(n["label"]) == want],
        [i for i, n in enumerate(nodes) if want and _norm_label(n["label"]).startswith(want)],
        [i for i, n in enumerate(nodes) if want and want in _norm_label(n["label"])],
    ]
    for tier in tiers:
        hits = sorted({lift(i) for i in tier} - {None})
        if kinds:
            hits = [i for i in hits if isinstance(nodes[i]["w"], kinds)]
        if not kinds and len(hits) > 1:
            # A button and the text inside it share a label: the control is what you mean. Otherwise keep the
            # innermost widget of each chain (a panel whose label is its one text block's).
            controls = [i for i in hits if isinstance(nodes[i]["w"], _CONTROLS)]
            outer = [i for i in controls if not any(_is_inside(nodes, i, j) for j in controls if j != i)]
            if outer:
                hits = outer
            else:
                hits = [i for i in hits if not any(_is_inside(nodes, j, i) for j in hits if j != i)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            listing = ", ".join("%s (%s) %s" % (nodes[i]["name"], nodes[i]["cls"], json.dumps(nodes[i]["label"][:40]))
                                for i in hits[:8])
            raise UIError("%r matches %d widgets: %s; use a name" % (target, len(hits), listing))
    labels = sorted({n["text"] for n in nodes if n["text"]} | {n["name"] for n in nodes if isinstance(n["w"], _CONTROLS)})
    raise UIError("nothing on screen matches %r%s. On screen: %s" % (
        target, " (looking for %s)" % "/".join(k.__name__ for k in kinds) if kinds else "",
        ", ".join(json.dumps(l[:30]) for l in labels[:25]) or "nothing"))


def _is_inside(nodes, inner, outer):
    j = nodes[inner]["parent"]
    while j is not None:
        if j == outer:
            return True
        j = nodes[j]["parent"]
    return False


def find(target, player=None, hidden=False):
    """The widget object a target names (module doc: by name or label)."""
    nodes = _walk(_world(), player, hidden)
    return nodes[_resolve(nodes, target)]["w"]


def _control(target, kind, player):
    nodes = _walk(_world(), player, False)
    n = nodes[_resolve(nodes, target, _KINDS[kind])]
    w = n["w"]
    if not w.get_is_enabled():
        raise UIError("%s (%s) is disabled" % (n["name"], n["cls"]))
    return w, n


def click(target, player=None):
    """Click a button or checkbox by label or name: OnPressed, OnReleased and OnClicked fire, as for a real click (a
    checkbox toggles and fires OnCheckStateChanged). Returns what was clicked."""
    w, n = _control(target, "click", player)
    if isinstance(w, unreal.CheckBox):
        state = not w.is_checked()
        w.set_is_checked(state)
        w.on_check_state_changed.broadcast(state)
        return {"clicked": n["name"], "class": n["cls"], "checked": state}
    w.on_pressed.broadcast()
    w.on_released.broadcast()
    w.on_clicked.broadcast()
    return {"clicked": n["name"], "class": n["cls"], "label": n["label"]}


def type(target, text, commit=True, player=None):
    """Type into an editable text: its text is set, OnTextChanged fires, and (commit=True) OnTextCommitted with Enter."""
    w, n = _control(target, "type", player)
    value = unreal.Text(text)
    w.set_text(value)
    w.on_text_changed.broadcast(value)
    if commit:
        w.on_text_committed.broadcast(value, unreal.TextCommit.ON_ENTER)
    return {"typed": n["name"], "text": text, "committed": commit}


def set(target, value, player=None):
    """Set a slider, spin box (a number), checkbox (a bool) or combo box (an option), firing its change event."""
    w, n = _control(target, "set", player)
    if isinstance(w, (unreal.Slider, unreal.SpinBox)):
        w.set_value(float(value))
        w.on_value_changed.broadcast(float(value))
    elif isinstance(w, unreal.CheckBox):
        w.set_is_checked(bool(value))
        w.on_check_state_changed.broadcast(bool(value))
    else:
        options = [str(w.get_option_at_index(i)) for i in range(w.get_option_count())]
        if str(value) not in options:
            raise UIError("%s has no option %r (options: %s)" % (n["name"], value, ", ".join(options)))
        w.set_selected_option(str(value))  # fires OnSelectionChanged itself
    return {"set": n["name"], "class": n["cls"], "value": _value(w) if _value(w) is not None else _own_text(w)}


def text(target=None, player=None):
    """One widget's label (its text, or the text inside it), or every text on screen, top to bottom."""
    nodes = _walk(_world(), player, False)
    if target is not None:
        return nodes[_resolve(nodes, target)]["label"]
    return [n["text"] for n in nodes if n["text"]]


def open(widget, player=0, z_order=0):
    """Put a user widget on screen in the running game, as Create Widget + Add to Viewport do: a Widget Blueprint
    path or a widget class, owned by local player `player`. Returns the widget."""
    world = _world()
    cls = _widget_class(widget) if isinstance(widget, str) else widget
    if not isinstance(cls, unreal.Class):
        cls = cls.static_class()
    pc = unreal.GameplayStatics.get_player_controller(world, int(player))
    made = _lib().create_widget(pc or world, cls)
    if made is None:
        raise UIError("could not create %s (a UserWidget class is needed)" % widget)
    made.add_to_viewport(int(z_order))
    return made


def wait(target, timeout=10.0, gone=False, player=None):
    """Scenario: `yield ui.wait('Paused')` resumes when a widget matching target is on screen (gone=True: when none
    is) and returns it. Timed in wall-clock seconds, so a paused game doesn't stall it."""
    import ueh

    def check():
        try:
            nodes = _walk(_world(), player, False)
            found = nodes[_resolve(nodes, target)]["w"]
        except UIError:
            found = None
        return (found is None) if gone else found

    return ueh.until(check, timeout=timeout, message="%s %r" % ("gone:" if gone else "on screen:", target))


# --------------------------------------------------------------------------- building Widget Blueprints

_HALIGN = {"left": "H_ALIGN_LEFT", "center": "H_ALIGN_CENTER", "right": "H_ALIGN_RIGHT", "fill": "H_ALIGN_FILL"}
_VALIGN = {"top": "V_ALIGN_TOP", "center": "V_ALIGN_CENTER", "bottom": "V_ALIGN_BOTTOM", "fill": "V_ALIGN_FILL"}


def _widget_class(name):
    if isinstance(name, str) and name.startswith("/"):
        obj = uprop.asset(name)
        if isinstance(obj, unreal.WidgetBlueprint):
            return obj.generated_class()
        if isinstance(obj, unreal.Class):
            return obj
        raise UIError("%s is not a Widget Blueprint" % name)
    cls = getattr(unreal, name, None)
    if cls is None or not issubclass(cls, unreal.Widget):
        raise UIError("no widget class %r (TextBlock, Button, VerticalBox, CanvasPanel... or a Widget Blueprint path)" % name)
    return cls.static_class()


def _margin(value):
    v = [float(value)] * 4 if isinstance(value, (int, float)) else [float(x) for x in value]
    if len(v) == 2:
        v = [v[0], v[1], v[0], v[1]]
    return unreal.Margin(left=v[0], top=v[1], right=v[2], bottom=v[3])


def _apply_text(w, spec):
    if "font_size" in spec:
        font = w.get_editor_property("font")
        font.set_editor_property("size", int(spec["font_size"]))
        w.set_editor_property("font", font)
    if "color" in spec:
        c = uprop.color(spec["color"])
        w.set_editor_property("color_and_opacity", unreal.SlateColor(specified_color=c))
    if "text" in spec:
        w.set_editor_property("text", unreal.Text(str(spec["text"])))


def _apply_slot(slot, spec):
    for key, value in (spec or {}).items():
        if isinstance(slot, unreal.CanvasPanelSlot) and key in ("position", "size", "alignment"):
            getattr(slot, "set_" + key)(unreal.Vector2D(*[float(v) for v in value]))
        elif isinstance(slot, unreal.CanvasPanelSlot) and key == "anchors":
            v = [float(x) for x in value]
            v = v * 2 if len(v) == 2 else v
            slot.set_anchors(unreal.Anchors(minimum=unreal.Vector2D(v[0], v[1]), maximum=unreal.Vector2D(v[2], v[3])))
        elif isinstance(slot, unreal.CanvasPanelSlot) and key == "auto_size":
            slot.set_auto_size(bool(value))
        elif isinstance(slot, unreal.CanvasPanelSlot) and key == "z_order":
            slot.set_z_order(int(value))
        elif key == "padding":
            slot.set_padding(_margin(value))
        elif key == "h_align":
            slot.set_horizontal_alignment(getattr(unreal.HorizontalAlignment, _HALIGN[value]))
        elif key == "v_align":
            slot.set_vertical_alignment(getattr(unreal.VerticalAlignment, _VALIGN[value]))
        elif key == "fill":
            slot.set_size(unreal.SlateChildSize(value=float(value), size_rule=unreal.SlateSizeRule.FILL))
        else:
            uprop.set_prop(slot, key, value)


def _add(wbp, parent, spec):
    cls = _widget_class(spec.get("type", "Border"))
    result = json.loads(_lib().add_widget(wbp, parent, cls, unreal.Name(spec.get("name") or "None")))
    if not result.pop("ok", False):
        raise UIError(result.get("error"))
    w = unreal.load_object(None, result["widget"])
    text_bits = {k: spec[k] for k in ("text", "font_size", "color") if k in spec}
    if text_bits and isinstance(w, unreal.TextBlock):
        _apply_text(w, text_bits)
    elif "text" in spec and isinstance(w, unreal.ContentWidget):  # a button's label: a text block inside it
        _add(wbp, w, dict({"type": "TextBlock", "name": (spec.get("name") or "Widget") + "Text"}, **text_bits))
    for key, value in (spec.get("props") or {}).items():
        uprop.set_prop(w, key, value)
    if result.get("slot") and spec.get("slot"):
        _apply_slot(unreal.load_object(None, result["slot"]), spec["slot"])
    for child in spec.get("children") or []:
        _add(wbp, w, child)
    return w


def build(spec, path, save=True):
    """Create a Widget Blueprint from a spec (module doc), or rebuild an existing one's designer tree in place (its
    graph stays; references to it stay valid). Compiles and saves. Returns {path, widgets, compile}."""
    if isinstance(spec, str):
        spec = json.loads(spec)
    import ueh
    import bpedit
    package = path.split(".")[0]
    if uprop.exists(package):
        wbp = uprop.asset(package)
        if not isinstance(wbp, unreal.WidgetBlueprint):
            raise UIError("%s exists and is a %s, not a Widget Blueprint" % (package, wbp.get_class().get_name()))
        root = _lib().get_root_widget(wbp)
        if root is not None:
            result = json.loads(_lib().remove_widget(wbp, unreal.Name(root.get_name())))
            if not result.get("ok"):
                raise UIError(result.get("error"))
    else:
        wbp = ueh.create_bp(spec.get("parent", "UserWidget"), package, save=False)
    if spec.get("root"):
        _add(wbp, None, spec["root"])
    compiled = bpedit.compile(package)
    names = [n for n in _design_nodes(wbp)]
    if save:
        ueh.save(package)
    return {"path": package, "widgets": len(names), "compile": compiled.get("status")}


def _design_nodes(wbp):
    out = []

    def visit(w, depth):
        out.append((w, depth))
        for child in _children(w):
            visit(child, depth + 1)

    root = _lib().get_root_widget(wbp)
    if root is not None:
        visit(root, 0)
    return out


_DESIGN_SKIP = {"slot", "is_variable", "navigation", "render_transform", "render_transform_pivot", "cursor",
                "flow_direction_preference", "font", "color_and_opacity", "text", "shaped_text_options"}
_HALIGN_NAMES = {v: k for k, v in _HALIGN.items()}
_VALIGN_NAMES = {v: k for k, v in _VALIGN.items()}


def _round(values):
    return [float(format(v, ".6g")) for v in values]


def _slot_spec(slot):
    """A slot's settings in build()'s keys, where they differ from the slot class's defaults."""
    cdo = unreal.get_default_object(slot.get_class())
    out = {}
    if isinstance(slot, unreal.CanvasPanelSlot):
        a, b = slot.get_anchors(), cdo.get_anchors()
        mine = _round([a.minimum.x, a.minimum.y, a.maximum.x, a.maximum.y])
        if mine != _round([b.minimum.x, b.minimum.y, b.maximum.x, b.maximum.y]):
            out["anchors"] = mine
        for key in ("position", "size", "alignment"):
            if key == "size" and slot.get_auto_size():
                continue
            v, d = getattr(slot, "get_" + key)(), getattr(cdo, "get_" + key)()
            if _round([v.x, v.y]) != _round([d.x, d.y]):
                out[key] = _round([v.x, v.y])
        if slot.get_auto_size():
            out["auto_size"] = True
        if slot.get_z_order():
            out["z_order"] = slot.get_z_order()
        return out
    for prop, key, names in (("horizontal_alignment", "h_align", _HALIGN_NAMES),
                             ("vertical_alignment", "v_align", _VALIGN_NAMES)):
        try:
            v, d = slot.get_editor_property(prop), cdo.get_editor_property(prop)
        except Exception:
            continue
        if v != d:
            out[key] = names.get(v.name, v.name.lower())
    try:
        pad = uprop.plain(slot.get_editor_property("padding"))
        if pad != uprop.plain(cdo.get_editor_property("padding")):
            out["padding"] = pad
    except Exception:
        pass
    try:
        size = slot.get_editor_property("size")
        if size.get_editor_property("size_rule") == unreal.SlateSizeRule.FILL:
            out["fill"] = float(format(size.get_editor_property("value"), ".6g"))
    except Exception:
        pass
    return out


def _describe_widget(w):
    node = {"type": w.get_class().get_name(), "name": w.get_name()}
    if isinstance(w, unreal.UserWidget) and w.get_class().get_name().endswith("_C"):
        node["type"] = uprop.short_path(w.get_class().get_path_name()).rsplit("_C", 1)[0]
    if isinstance(w, unreal.TextBlock):
        cdo = unreal.get_default_object(w.get_class())
        node["text"] = str(w.get_text())
        size = w.get_editor_property("font").get_editor_property("size")
        if size != cdo.get_editor_property("font").get_editor_property("size"):
            node["font_size"] = size
        color = uprop.plain(w.get_editor_property("color_and_opacity").get_editor_property("specified_color"))
        if color != uprop.plain(cdo.get_editor_property("color_and_opacity").get_editor_property("specified_color")):
            node["color"] = color
    props = uprop.nondefault(w, _DESIGN_SKIP)
    if props:
        node["props"] = props
    slot = w.get_editor_property("slot")
    if slot is not None:
        sp = _slot_spec(slot)
        if sp:
            node["slot"] = sp
    kids = [_describe_widget(c) for c in _children(w)] if not isinstance(w, unreal.UserWidget) else []
    if kids:
        node["children"] = kids
    return node


def _parent_name(bp):
    """A Blueprint's parent class name (get_blueprint_parent_class is 5.8+; the asset registry has it everywhere)."""
    getter = getattr(unreal.BlueprintEditorLibrary, "get_blueprint_parent_class", None)
    if getter is not None:
        parent = getter(bp)
        return parent.get_name() if parent else None
    reg = unreal.AssetRegistryHelpers.get_asset_registry()
    for data in reg.get_assets_by_package_name(unreal.Name(bp.get_path_name().split(".")[0])):
        value = str(data.get_tag_value("ParentClass") or "")
        if value:
            return value.rstrip("'").split(".")[-1]
    return None


def describe(path, text=True):
    """A Widget Blueprint's designer tree: each widget's type, name, text, non-default properties and slot settings.
    text=False returns the dict (a SPEC whose props and slots are in Unreal's own property names)."""
    wbp = uprop.asset(path)
    if not isinstance(wbp, unreal.WidgetBlueprint):
        raise UIError("%s is not a Widget Blueprint" % path)
    root = _lib().get_root_widget(wbp)
    spec = {"parent": _parent_name(wbp),
            "root": _describe_widget(root) if root else None}
    if not text:
        return spec
    lines = ["%s (parent %s)" % (wbp.get_name(), spec["parent"])]

    def emit(node, depth):
        bits = ["%s%s (%s)" % ("  " * depth, node["name"], node["type"])]
        if "text" in node:
            bits.append(json.dumps(node["text"]))
        for key in ("font_size", "color"):
            if key in node:
                bits.append("%s=%s" % (key, json.dumps(node[key])))
        if node.get("props"):
            bits.append(" ".join("%s=%s" % (k, json.dumps(v)) for k, v in node["props"].items()))
        if node.get("slot"):
            bits.append("slot: " + " ".join("%s=%s" % (k, json.dumps(v)) for k, v in node["slot"].items()))
        lines.append("  ".join(bits))
        for child in node.get("children", []):
            emit(child, depth + 1)

    if spec["root"]:
        emit(spec["root"], 1)
    else:
        lines.append("  (empty)")
    return "\n".join(lines)
