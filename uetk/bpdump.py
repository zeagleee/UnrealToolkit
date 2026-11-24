"""Condense Blueprint T3D exports into readable text for review (`ue.py bpdump`). System Python.

Input (written by `ueh.export_blueprints` in the editor), per asset in raw_dir:
  <name>.t3d         the Blueprint (or data asset / struct / enum)
  <name>.cdo.t3d     its class default object
  <name>.parent.t3d  the parent class's default object, so only overridden defaults are listed
  <name>.json        a data table
Output in out_dir: <name>.txt per asset, the data-table .json files, and _index.txt.

Each Blueprint .txt has: parent class, interfaces, event dispatchers, variables (replication flags, defaults),
components added in the Blueprint, class defaults that differ from the parent, the widget tree and property
bindings (widgets), then every source graph as pseudo-code:
  EVENT/CUSTOM EVENT/ON/INPUT/FUNCTION headers   one block per entry node, with params and RPC flags
  nN: statement                                  per exec node; `nN.Pin` refers to that node's output later
  [PinName]:                                     branch outputs (Branch, Sequence, Cast, ForEach, Switch...)
  → goto nN                                      flow joins a node printed earlier
  ∅                                              an unset/empty input
  Unreachable exec nodes                         nodes with exec pins that no entry reaches (disconnected logic)
Pure nodes are inlined as expressions. Pin ids repeat across copy-pasted nodes, so links resolve by node
name first. Widget trees are nested through each slot's Parent/Content.

Standalone: python bpdump.py <raw_dir> <out_dir>
"""
import os
import re
import shutil
import sys

BLUEPRINT_ROOT_CLASSES = ("Blueprint", "WidgetBlueprint", "AnimBlueprint", "LevelScriptBlueprint",
                          "EditorUtilityBlueprint", "EditorUtilityWidgetBlueprint", "ControlRigBlueprint")

ATTR_RE = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')
ENTRY_CLASSES = {"K2Node_Event", "K2Node_CustomEvent", "K2Node_ComponentBoundEvent", "K2Node_ActorBoundEvent",
                 "K2Node_InputKey", "K2Node_InputAction", "K2Node_InputAxisEvent", "K2Node_EnhancedInputAction",
                 "K2Node_FunctionEntry", "K2Node_InputTouch", "K2Node_InputDebugKey", "K2Node_Tunnel"}
EVENT_NAMES = {"ReceiveBeginPlay": "BeginPlay", "ReceiveTick": "Tick", "ReceiveEndPlay": "EndPlay",
               "ReceiveDestroyed": "Destroyed", "Construct": "Construct", "Destruct": "Destruct",
               "PreConstruct": "PreConstruct", "ReceivePossess": "Possess", "ReceiveUnPossess": "UnPossess",
               "ReceiveActorBeginOverlap": "ActorBeginOverlap", "ReceiveAnyDamage": "AnyDamage",
               "BlueprintUpdateAnimation": "UpdateAnimation", "BlueprintInitializeAnimation": "InitializeAnimation",
               "OnInitialized": "OnInitialized"}
MAX_EXPR = 180
MAX_LINE = 320


# ----------------------------------------------------------------------------- text helpers
def split_top(s):
    out, cur, depth, q, esc = [], [], 0, False, False
    for ch in s:
        if q:
            cur.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                q = False
            continue
        if ch == '"':
            q = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    if cur:
        out.append("".join(cur))
    return [x for x in out if x.strip()]


def kv(s):
    d = {}
    for part in split_top(s):
        if "=" in part:
            k, v = part.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def unq(v):
    if v is None:
        return None
    v = v.strip()
    m = re.match(r'^(?:NSLOCTEXT|LOCTEXT)\((.*)\)$', v, re.S)
    if m:
        parts = split_top(m.group(1))
        return unq(parts[-1]) if parts else ""
    m = re.match(r'^INVTEXT\((.*)\)$', v, re.S)
    if m:
        return unq(m.group(1))
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return v


def short(v):
    """Object/class reference -> short name."""
    v = unq(v) or ""
    m = re.search(r"'([^']*)'", v)
    if m:
        v = m.group(1)
    v = v.strip('"')
    tail = v.split(":")[-1]
    name = tail.split(".")[-1] if "." in tail else tail.split("/")[-1]
    if name.endswith("_C"):
        name = name[:-2]
    if name.startswith("Default__"):
        name = name[9:]
    return name


def clip(s, n=MAX_EXPR):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


# ----------------------------------------------------------------------------- T3D parsing
class Obj:
    __slots__ = ("path", "cls", "props", "pins")

    def __init__(self, path, cls):
        self.path, self.cls, self.props, self.pins = path, cls, [], []

    def get(self, key, default=None):
        for k, v in self.props:
            if k == key:
                return v
        return default

    def all(self, prefix):
        return [(k, v) for k, v in self.props if k == prefix or k.startswith(prefix + "(")]


def parse_t3d(path):
    objs, order, stack = {}, [], []
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        for raw in f:
            s = raw.rstrip("\r\n")
            st = s.strip()
            if st.startswith("Begin Object"):
                attrs = {k: v for k, v in ATTR_RE.findall(st[len("Begin Object"):])}
                exp = unq(attrs.get("ExportPath", "")) or ""
                cls, opath = "", ""
                m = re.match(r"^(.*?)'(.*)'$", exp)
                if m:
                    cls, opath = m.group(1).split(".")[-1], m.group(2)
                else:
                    opath = (stack[-1].path + "." if stack else "") + (unq(attrs.get("Name", "")) or "?")
                if "Class" in attrs:
                    cls = attrs["Class"].split(".")[-1]
                o = objs.get(opath)
                if o is None:
                    o = objs[opath] = Obj(opath, cls)
                    order.append(o)
                elif cls and not o.cls:
                    o.cls = cls
                stack.append(o)
            elif st == "End Object":
                if stack:
                    stack.pop()
            elif stack:
                if st.startswith("CustomProperties Pin ("):
                    stack[-1].pins.append(parse_pin(st[len("CustomProperties Pin ("):-1]))
                elif st.startswith("CustomProperties"):
                    continue
                elif "=" in st:
                    k, v = st.split("=", 1)
                    stack[-1].props.append((k, v))
    return objs, order


def parse_pin(s):
    d = kv(s)
    links = []
    lt = d.get("LinkedTo")
    if lt:
        for item in split_top(lt.strip()[1:-1]):
            parts = item.strip().split()
            if len(parts) == 2:
                links.append((parts[0], parts[1]))
    return {
        "id": d.get("PinId"),
        "name": unq(d.get("PinName", "")),
        "friendly": unq(d.get("PinFriendlyName")) if d.get("PinFriendlyName") else None,
        "out": d.get("Direction") == '"EGPD_Output"',
        "cat": unq(d.get("PinType.PinCategory", "")),
        "subobj": d.get("PinType.PinSubCategoryObject"),
        "container": d.get("PinType.ContainerType"),
        "default": unq(d.get("DefaultValue")) if d.get("DefaultValue") is not None else "",
        "default_obj": d.get("DefaultObject"),
        "default_text": unq(d.get("DefaultTextValue")) if d.get("DefaultTextValue") else "",
        "autogen": unq(d.get("AutogeneratedDefaultValue")) if d.get("AutogeneratedDefaultValue") else "",
        "hidden": d.get("bHidden") == "True",
        "advanced": d.get("bAdvancedView") == "True",
        "orphan": d.get("bOrphanedPin") == "True",
        "links": links,
    }


# ----------------------------------------------------------------------------- graph model
class Node:
    def __init__(self, name, obj):
        self.name, self.obj, self.cls = name, obj, obj.cls
        self.pins = [p for p in obj.pins if not p["orphan"]]

    def exec_pins(self, out):
        return [p for p in self.pins if p["cat"] == "exec" and p["out"] == out and not p["hidden"]]

    @property
    def is_pure(self):
        return not any(p["cat"] == "exec" for p in self.pins)

    def pin(self, name):
        for p in self.pins:
            if p["name"] == name:
                return p
        return None


class Graph:
    def __init__(self, gpath):
        self.path, self.nodes, self.pin_index, self.comments = gpath, {}, {}, []

    def add(self, node):
        self.nodes[node.name] = node
        for p in node.pins:
            self.pin_index[p["id"]] = (node, p)

    def resolve(self, link):
        # Pin ids are not unique across copy-pasted nodes: resolve by node name first, then the pin within it.
        name, pid = link
        node = self.nodes.get(name)
        if node:
            for p in node.pins:
                if p["id"] == pid:
                    return node, p
            return node, None
        hit = self.pin_index.get(pid)
        return hit if hit else (None, None)


class Printer:
    def __init__(self, graph, rpc_flags):
        self.g, self.labels, self.printed, self.lines, self.rpc_flags = graph, {}, set(), [], rpc_flags

    def label(self, node):
        if node.name not in self.labels:
            self.labels[node.name] = "n%d" % (len(self.labels) + 1)
        return self.labels[node.name]

    # ---- titles
    def func_name(self, node):
        ref = kv(node.obj.get("FunctionReference", "()")[1:-1])
        name = unq(ref.get("MemberName", "")) or node.cls.replace("K2Node_", "")
        parent = short(ref["MemberParent"]) if "MemberParent" in ref else None
        return name, parent, ref.get("bSelfContext") == "True"

    def title(self, node):
        c, o = node.cls, node.obj
        if c in ("K2Node_CallFunction", "K2Node_CallArrayFunction", "K2Node_PromotableOperator",
                 "K2Node_CommutativeAssociativeBinaryOperator", "K2Node_CallMaterialParameterCollectionFunction",
                 "K2Node_CallFunctionOnMember", "K2Node_AddComponent"):
            name, parent, _ = self.func_name(node)
            return name
        if c == "K2Node_CallParentFunction":
            return "Super::" + self.func_name(node)[0]
        if c in ("K2Node_VariableGet", "K2Node_VariableSet", "K2Node_StructMemberGet"):
            ref = kv(o.get("VariableReference", "()")[1:-1])
            return unq(ref.get("MemberName", "?"))
        if c == "K2Node_Event":
            ref = kv(o.get("EventReference", "()")[1:-1])
            n = unq(ref.get("MemberName", "?"))
            return EVENT_NAMES.get(n, n)
        if c == "K2Node_CustomEvent":
            return unq(o.get("CustomFunctionName", "?"))
        if c == "K2Node_ComponentBoundEvent":
            return "%s.%s" % (unq(o.get("ComponentPropertyName", "?")), unq(o.get("DelegatePropertyName", "?")))
        if c == "K2Node_ActorBoundEvent":
            return "%s.%s" % (short(o.get("EventOwner", "?")), unq(o.get("DelegatePropertyName", "?")))
        if c == "K2Node_InputKey":
            return "Key " + unq(o.get("InputKey", "?"))
        if c == "K2Node_InputAction":
            return "InputAction " + unq(o.get("InputActionName", "?"))
        if c == "K2Node_EnhancedInputAction":
            return "EnhancedInput " + short(o.get("InputAction", "?"))
        if c == "K2Node_MacroInstance":
            ref = kv(o.get("MacroGraphReference", "()")[1:-1])
            return short(ref.get("MacroGraph", "Macro"))
        if c in ("K2Node_DynamicCast", "K2Node_ClassDynamicCast"):
            return "Cast<%s>" % short(o.get("TargetType", "?"))
        if c == "K2Node_IfThenElse":
            return "Branch"
        if c == "K2Node_ExecutionSequence":
            return "Sequence"
        if c in ("K2Node_CreateWidget", "K2Node_SpawnActorFromClass", "K2Node_ConstructObjectFromClass",
                 "K2Node_GetDataTableRow", "K2Node_LoadAsset"):
            cp = node.pin("Class") or node.pin("DataTable")
            target = short(cp["default_obj"]) if cp and cp["default_obj"] else ("?" if not cp or not cp["links"] else self.expr_in(cp, 1))
            return "%s<%s>" % (c.replace("K2Node_", ""), target)
        if c == "K2Node_GetSubsystem" or c == "K2Node_GetSubsystemFromPC":
            return "GetSubsystem<%s>" % short(o.get("CustomClass", "?"))
        if c in ("K2Node_MakeStruct", "K2Node_BreakStruct", "K2Node_SetFieldsInStruct"):
            return "%s<%s>" % (c.replace("K2Node_", ""), short(o.get("StructType", "?")))
        if c in ("K2Node_SwitchEnum",):
            return "Switch<%s>" % short(o.get("Enum", "?"))
        if c in ("K2Node_AddDelegate", "K2Node_RemoveDelegate", "K2Node_ClearDelegate", "K2Node_CallDelegate",
                 "K2Node_AssignDelegate"):
            ref = kv(o.get("DelegateReference", "()")[1:-1])
            verb = {"K2Node_AddDelegate": "Bind", "K2Node_RemoveDelegate": "Unbind", "K2Node_ClearDelegate": "UnbindAll",
                    "K2Node_CallDelegate": "Broadcast", "K2Node_AssignDelegate": "Bind+Event"}[c]
            return "%s %s" % (verb, unq(ref.get("MemberName", "?")))
        if c == "K2Node_CreateDelegate":
            return "Delegate(%s)" % unq(o.get("SelectedFunctionName", "?"))
        if c == "K2Node_Timeline":
            return "Timeline " + unq(o.get("TimelineName", "?"))
        if c == "K2Node_FunctionResult":
            return "return"
        if c == "K2Node_Self":
            return "self"
        if c == "K2Node_Literal":
            return short(o.get("ObjectRef", "?"))
        if c == "K2Node_AsyncAction" or c == "K2Node_BaseAsyncTask":
            return unq(o.get("ProxyFactoryFunctionName", c.replace("K2Node_", "")))
        return c.replace("K2Node_", "")

    # ---- expressions
    def default_of(self, p):
        if p["default_obj"] and p["default_obj"] != "None":
            return short(p["default_obj"])
        if p["default_text"]:
            return '"%s"' % p["default_text"]
        v = p["default"]
        if v == "":
            return None
        if p["cat"] in ("string", "name", "text"):
            return '"%s"' % v
        return v

    def expr_in(self, pin, depth):
        if pin["links"]:
            parts = []
            for link in pin["links"][:3]:
                node, sp = self.g.resolve(link)
                parts.append(self.expr_out(node, sp, depth) if node else "?")
            return clip(" | ".join(parts))
        d = self.default_of(pin)
        return d if d is not None else "∅"

    def expr_out(self, node, pin, depth):
        c = node.cls
        pname = pin["name"] if pin else "?"
        if c == "K2Node_Knot":
            ip = next((p for p in node.pins if not p["out"]), None)
            return self.expr_in(ip, depth) if ip else "?"
        if c == "K2Node_Self":
            return "self"
        if c in ENTRY_CLASSES:
            return "%s.%s" % (self.title(node) if c != "K2Node_FunctionEntry" else "arg", pname)
        if c in ("K2Node_VariableGet", "K2Node_StructMemberGet") and node.is_pure:
            tgt = node.pin("self")
            base = self.title(node)
            if tgt and tgt["links"]:
                return clip("%s.%s" % (self.expr_in(tgt, depth + 1), base))
            return base
        if not node.is_pure:
            return "%s.%s" % (self.label(node), pname)
        if depth > 4:
            return "…"
        if c == "K2Node_BreakStruct":
            ip = next((p for p in node.pins if not p["out"]), None)
            return clip("%s.%s" % (self.expr_in(ip, depth + 1) if ip else "?", pname))
        if c == "K2Node_Literal":
            return self.title(node)
        args = self.args(node, depth + 1)
        outs = [p for p in node.pins if p["out"] and not p["hidden"]]
        suffix = "" if len(outs) <= 1 or pname == "ReturnValue" else "." + pname
        return clip("%s(%s)%s" % (self.title(node), args, suffix))

    def args(self, node, depth, skip=()):
        out = []
        for p in node.pins:
            if p["out"] or p["cat"] == "exec" or p["hidden"] or p["name"] in skip:
                continue
            if p["name"] in ("self", "WorldContextObject", "__WorldContext") and not p["links"]:
                continue
            if p["links"]:
                v = self.expr_in(p, depth)
            else:
                d = self.default_of(p)
                if d is None or (p["advanced"] and p["default"] == p["autogen"]):
                    continue
                if p["cat"] == "object" and d in ("None", ""):
                    continue
                v = d
            label = "Target" if p["name"] == "self" else (p["friendly"] or p["name"])
            out.append("%s=%s" % (label, v))
        return ", ".join(out)

    # ---- statements
    def statement(self, node):
        c, t = node.cls, self.title(node)
        lab = self.label(node)
        if c == "K2Node_VariableSet":
            tgt = node.pin("self")
            val = next((p for p in node.pins if not p["out"] and p["cat"] != "exec" and p["name"] != "self"), None)
            lhs = ("%s.%s" % (self.expr_in(tgt, 1), t)) if tgt and tgt["links"] else t
            return "%s: %s = %s" % (lab, lhs, self.expr_in(val, 1) if val else "?")
        if c == "K2Node_IfThenElse":
            return "%s: if (%s)" % (lab, self.expr_in(node.pin("Condition"), 1))
        if c == "K2Node_FunctionResult":
            return "%s: return (%s)" % (lab, self.args(node, 1))
        skip = ("Class",) if c in ("K2Node_CreateWidget", "K2Node_SpawnActorFromClass", "K2Node_ConstructObjectFromClass") else ()
        if c in ("K2Node_AddDelegate", "K2Node_AssignDelegate", "K2Node_RemoveDelegate"):
            return "%s: %s(%s)" % (lab, t, self.args(node, 1))
        target = ""
        if c.startswith("K2Node_Call") and c != "K2Node_CallDelegate":
            name, parent, selfctx = self.func_name(node)
            if parent and not selfctx and not (node.pin("self") and node.pin("self")["links"]):
                target = parent + "::"
        return "%s: %s%s(%s)" % (lab, target, t, self.args(node, 1, skip))

    def walk(self, pin, indent):
        pad = "  " * indent
        for link in pin["links"]:
            node, tp = self.g.resolve(link)
            while node is not None and node.cls == "K2Node_Knot":
                op = next((p for p in node.pins if p["out"]), None)
                if not op or not op["links"]:
                    node = None
                    break
                node, tp = self.g.resolve(op["links"][0])
            if node is None:
                continue
            via = "" if not tp or tp["name"] in ("execute", "exec", "Exec", "") else "  ⟵ via '%s'" % (tp["friendly"] or tp["name"])
            if node.name in self.printed:
                self.lines.append("%s→ goto %s%s" % (pad, self.label(node), via))
                continue
            self.printed.add(node.name)
            self.lines.append(pad + clip(self.statement(node) + via, MAX_LINE))
            outs = [p for p in node.exec_pins(True) if p["links"]]
            if len(outs) == 1 and len(node.exec_pins(True)) == 1:
                self.walk(outs[0], indent)
            else:
                for p in outs:
                    self.lines.append("%s  [%s]:" % (pad, p["friendly"] or p["name"]))
                    self.walk(p, indent + 2)

    def entry_header(self, node):
        c = node.cls
        params = [p for p in node.pins if p["out"] and p["cat"] not in ("exec", "delegate") and not p["hidden"]]
        ps = ", ".join("%s:%s" % (p["name"], pin_type(p)) for p in params)
        if c == "K2Node_FunctionEntry":
            return "FUNCTION %s(%s)" % (self.g.path.split(".")[0], ps)
        kind = {"K2Node_Event": "EVENT", "K2Node_CustomEvent": "CUSTOM EVENT", "K2Node_ComponentBoundEvent": "ON",
                "K2Node_ActorBoundEvent": "ON", "K2Node_Tunnel": "TUNNEL"}.get(c, "INPUT")
        extra = ""
        if c == "K2Node_CustomEvent":
            extra = self.rpc_flags(node.obj.get("FunctionFlags"))
        if c == "K2Node_Event":
            ref = kv(node.obj.get("EventReference", "()")[1:-1])
            if "MemberParent" in ref:
                extra = " (from %s)" % short(ref["MemberParent"])
        if c == "K2Node_InputKey":
            pass
        return "%s %s(%s)%s" % (kind, self.title(node), ps, extra)


def pin_type(p):
    t = p["cat"]
    if p["subobj"] and p["subobj"] != "None":
        t = short(p["subobj"])
    if p["container"] and p["container"] != "None":
        t = "%s<%s>" % (p["container"], t)
    return t


def rpc_flags(flags):
    try:
        f = int(flags)
    except (TypeError, ValueError):
        return ""
    tags = []
    if f & 0x00200000:
        tags.append("Server")
    if f & 0x01000000:
        tags.append("Client")
    if f & 0x00004000:
        tags.append("Multicast")
    if tags:
        tags.append("Reliable" if f & 0x80 else "Unreliable")
    return " [RPC %s]" % " ".join(tags) if tags else ""


# ----------------------------------------------------------------------------- per-blueprint report
def top_props(path):
    """Top-level properties of a CDO export, as an ordered dict (first value per key)."""
    if not path or not os.path.exists(path):
        return {}, []
    _, order = parse_t3d(path)
    if not order:
        return {}, []
    d = {}
    for k, v in order[0].props:
        d.setdefault(k, v)
    return d, order


def condense_data(order):
    root = order[0]
    out = ["# %s  (%s)" % (root.path.split(".")[-1], root.cls), "Asset: %s" % root.path.split(".")[0], ""]
    for o in order:
        if not o.props:
            continue
        if o is not root:
            out.append("[%s %s]" % (o.cls, o.path.split(".")[-1]))
        for k, v in o.props[:200]:
            out.append("- %s = %s" % (k, clip(v, 400)))
    return "\n".join(out) + "\n"


def condense(t3d_path, cdo_path):
    objs, order = parse_t3d(t3d_path)
    root = order[0]
    if root.cls not in BLUEPRINT_ROOT_CLASSES:
        return root.path.split(".")[-1], root.cls, "", condense_data(order)
    asset_path = root.path
    out = []
    bp_name = asset_path.split(".")[-1]
    cdo, cdo_order = top_props(cdo_path)
    parent_cdo, _ = top_props(cdo_path.replace(".cdo.t3d", ".parent.t3d") if cdo_path else None)
    out.append("# %s  (%s)" % (bp_name, root.cls))
    out.append("Asset: %s" % asset_path.split(".")[0])
    parent = root.get("ParentClass")
    if parent:
        out.append("Parent class: %s" % short(parent))
    ifaces = root.all("ImplementedInterfaces")
    if ifaces:
        out.append("Interfaces: " + ", ".join(short(kv(v[1:-1]).get("Interface", "?")) for _, v in ifaces))

    source_graphs = []
    for key in ("UbergraphPages", "FunctionGraphs", "MacroGraphs", "DelegateSignatureGraphs"):
        for _, v in root.all(key):
            source_graphs.append((key, short(v)))
    dispatchers = [g for k, g in source_graphs if k == "DelegateSignatureGraphs"]
    if dispatchers:
        out.append("Event dispatchers: " + ", ".join(dispatchers))

    # variables
    vars_ = root.all("NewVariables")
    if vars_:
        out.append("")
        out.append("## Variables (%d)" % len(vars_))
        for _, v in vars_:
            d = kv(v[1:-1])
            vt = kv(d.get("VarType", "()")[1:-1])
            t = unq(vt.get("PinCategory", "?"))
            if vt.get("PinSubCategoryObject") and vt.get("PinSubCategoryObject") != "None":
                t = short(vt["PinSubCategoryObject"])
            if vt.get("ContainerType") and vt.get("ContainerType") != "None":
                t = "%s<%s>" % (vt["ContainerType"], t)
            flags = []
            try:
                pf = int(d.get("PropertyFlags", "0"))
                if pf & 0x20:
                    flags.append("Replicated")
                if not pf & 0x10000:  # CPF_DisableEditOnInstance absent -> instance editable
                    flags.append("InstanceEditable")
                if pf & 0x0001000000000000:
                    flags.append("ExposeOnSpawn")
            except ValueError:
                pass
            if d.get("RepNotifyFunc") and unq(d["RepNotifyFunc"]) not in ("None", ""):
                flags.append("RepNotify=" + unq(d["RepNotifyFunc"]))
            vname = unq(d.get("VarName", "?"))
            default = cdo.get(vname.replace(" ", ""), cdo.get(vname)) or unq(d.get("DefaultValue", "")) or ""
            if default in ("None", "False", "0", "0.000000", '""', "()"):
                default = ""
            out.append("- %s: %s%s%s" % (vname, t,
                                        (" = " + clip(default, 80)) if default else "",
                                        (" [" + ", ".join(flags) + "]") if flags else ""))

    # components (SCS)
    comps = [o for o in order if o.cls == "SCS_Node"]
    if comps:
        out.append("")
        out.append("## Components added in this Blueprint")
        for o in comps:
            out.append("- %s (%s)%s" % (unq(o.get("InternalVariableName", "?")), short(o.get("ComponentClass", "?")),
                                        (" under " + unq(o.get("ParentComponentOrVariableName"))) if o.get("ParentComponentOrVariableName") else ""))

    # widget tree
    wt = [o for o in order if ":WidgetTree." in o.path and o.cls and not o.cls.endswith("Slot")]
    if wt:
        out.append("")
        out.append("## Widget tree (%d widgets)" % len(wt))
        out.extend(widget_tree(objs, order))
    binds = root.all("Bindings")
    if binds:
        out.append("")
        out.append("## Property bindings (evaluated every frame)")
        for _, v in binds:
            d = kv(v[1:-1])
            out.append("- %s.%s <- %s" % (unq(d.get("ObjectName", "?")), unq(d.get("PropertyName", "?")),
                                         unq(d.get("FunctionName", "")) or unq(d.get("SourceProperty", "")) or "?"))
    anims = [o for o in order if o.cls == "WidgetAnimation" and o.path.count(":") == 1 and o.path.split(":")[-1].count(".") == 0]
    if anims:
        out.append("Widget animations: " + ", ".join(o.path.split(":")[-1] for o in anims))

    # class default overrides: CDO values that differ from the parent class's CDO (BP variables excluded)
    bp_vars = set()
    for _, v in vars_:
        n = unq(kv(v[1:-1]).get("VarName", ""))
        bp_vars.update({n, n.replace(" ", "")})
    if cdo:
        lines = [(k, v) for k, v in cdo.items()
                 if k not in bp_vars and not k.startswith(("UberGraphFrame",)) and parent_cdo.get(k) != v
                 and not (k not in parent_cdo and v in ("None", "False", "0", "0.000000", "()", '""'))]
        sub = [o for o in cdo_order[1:] if o.props and o.path.count(".") >= 1]
        if lines or sub:
            out.append("")
            out.append("## Class defaults that differ from the parent class")
            for k, v in lines[:80]:
                out.append("- %s = %s" % (k, clip(v, 220)))
            for o in sub[:25]:
                sl = ["%s=%s" % (k, clip(v, 90)) for k, v in o.props[:10]
                      if not k.startswith(("BodyInstance", "AttachParent", "RelativeScale3D=(X=1"))]
                if sl:
                    out.append("- [component %s : %s] %s" % (o.path.split(".")[-1], o.cls or "?", "; ".join(sl)))

    # graphs
    graphs = {}
    for o in order:
        if ":" not in o.path:
            continue
        inner = o.path.split(":", 1)[1]
        if "." not in inner:
            continue
        gpath, name = inner.rsplit(".", 1)
        if o.cls == "EdGraphNode_Comment":
            graphs.setdefault(gpath, Graph(gpath)).comments.append(unq(o.get("NodeComment", "")))
        elif o.cls.startswith("K2Node") or o.cls.startswith("AnimGraphNode"):
            graphs.setdefault(gpath, Graph(gpath)).add(Node(name, o))
    src_names = {g for _, g in source_graphs}
    for key, gname in source_graphs:
        if key == "DelegateSignatureGraphs":
            continue
        related = [gp for gp in graphs if gp == gname or gp.startswith(gname + ".")]
        for gp in sorted(related, key=lambda x: (x != gname, x)):
            g = graphs[gp]
            out.append("")
            out.append("## %s: %s  (%d nodes)" % ({"UbergraphPages": "Event graph", "FunctionGraphs": "Function",
                                                    "MacroGraphs": "Macro"}[key], gp, len(g.nodes)))
            for cmt in g.comments:
                if cmt:
                    out.append("  // " + clip(cmt, 200))
            anim_nodes = [n for n in g.nodes.values() if n.cls.startswith("AnimGraphNode")]
            if anim_nodes:
                counts = {}
                for n in anim_nodes:
                    counts[n.cls] = counts.get(n.cls, 0) + 1
                out.append("  anim nodes: " + ", ".join("%s×%d" % (k.replace("AnimGraphNode_", ""), v) for k, v in sorted(counts.items())))
            pr = Printer(g, rpc_flags)
            entries = [n for n in g.nodes.values() if n.cls in ENTRY_CLASSES or
                       (n.exec_pins(True) and not n.exec_pins(False) and n.cls != "K2Node_Knot")]
            entries.sort(key=lambda n: (int(n.obj.get("NodePosY", "0") or 0), int(n.obj.get("NodePosX", "0") or 0)))
            for e in entries:
                pr.printed.add(e.name)
                pr.lines.append("")
                pr.lines.append("  " + pr.entry_header(e))
                outs = [p for p in e.exec_pins(True) if p["links"]]
                if not outs:
                    pr.lines.append("    (nothing connected)")
                if len(outs) == 1 and len(e.exec_pins(True)) == 1:
                    pr.walk(outs[0], 2)
                else:
                    for p in outs:
                        pr.lines.append("    [%s]:" % (p["friendly"] or p["name"]))
                        pr.walk(p, 3)
            out.extend(pr.lines)
            loose = [n for n in g.nodes.values() if not n.is_pure and n.name not in pr.printed
                     and n.cls not in ("K2Node_Knot",) and not n.cls.startswith("AnimGraphNode")]
            if loose:
                out.append("")
                out.append("  Unreachable exec nodes (%d): %s" % (len(loose), clip(", ".join(pr.title(n) for n in loose), 400)))
    return bp_name, root.cls, short(parent) if parent else "", "\n".join(out) + "\n"


def widget_tree(objs, order):
    by_path = {o.path: o for o in order}
    children, has_parent = {}, set()
    for o in order:
        if ":WidgetTree." in o.path and o.cls.endswith("Slot"):
            par, con = o.get("Parent"), o.get("Content")
            if par and con:
                pp, cp = _ref_path(par, o.path), _ref_path(con, o.path)
                children.setdefault(pp, []).append(cp)
                has_parent.add(cp)
    widgets = [o for o in order if ":WidgetTree." in o.path and o.cls and not o.cls.endswith("Slot")
               and o.path.split(":WidgetTree.")[1].count(".") == 0]
    roots = [w for w in widgets if w.path not in has_parent]
    lines = []

    def emit(o, depth):
        extra = []
        for key in ("Text", "ToolTipText", "HintText"):
            v = o.get(key)
            if v:
                extra.append('%s="%s"' % (key, clip(unq(v), 60)))
        if o.get("bIsVariable") == "True":
            extra.append("var")
        vis = o.get("Visibility")
        if vis:
            extra.append("Visibility=" + vis)
        lines.append("%s- %s : %s %s" % ("  " * depth, o.path.split(".")[-1], o.cls, " ".join(extra)))
        for cp in children.get(o.path, []):
            c = by_path.get(cp)
            if c and depth < 14:
                emit(c, depth + 1)

    for r in roots:
        emit(r, 0)
    return lines


def _ref_path(v, ctx_path):
    """Slot Parent/Content references are relative ("WB_X:WidgetTree.Panel"); make them full object paths."""
    v = unq(v)
    m = re.search(r"'([^']*)'", v)
    ref = m.group(1) if m else v
    base = ctx_path.split(":", 1)[0]
    inner = ref.split(":", 1)[1] if ":" in ref else "WidgetTree." + ref.split(".")[-1]
    return base + ":" + inner


def condense_dir(raw_dir, out_dir):
    """Condense every export in raw_dir into out_dir. Returns the index lines (also written to _index.txt)."""
    os.makedirs(out_dir, exist_ok=True)
    index = []
    for fn in sorted(os.listdir(raw_dir)):
        if fn.endswith(".json") and not fn.startswith("_"):
            shutil.copyfile(os.path.join(raw_dir, fn), os.path.join(out_dir, fn))
            index.append("%-60s %-22s" % (fn[:-5], "DataTable (json)"))
            continue
        if not fn.endswith(".t3d") or fn.endswith((".cdo.t3d", ".parent.t3d")):
            continue
        base = fn[:-4]
        try:
            name, cls, parent, text = condense(os.path.join(raw_dir, fn), os.path.join(raw_dir, base + ".cdo.t3d"))
        except Exception as e:  # keep going; report the failure
            index.append("%-60s FAILED: %s" % (base, e))
            continue
        with open(os.path.join(out_dir, base + ".txt"), "w", encoding="utf-8") as f:
            f.write(text)
        index.append("%-60s %-22s parent=%-32s %5d lines" % (base, cls, parent, text.count("\n")))
    with open(os.path.join(out_dir, "_index.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(index) + "\n")
    return index


def main():
    print("\n".join(condense_dir(sys.argv[1], sys.argv[2])))


if __name__ == "__main__":
    main()
