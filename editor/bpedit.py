"""bpedit - edit Blueprint graphs from Python (UETKEditor plugin). Pre-imported as `bpedit` in `ue.py py`.

    g = bpedit.graph('/Game/BP_Door')                        # the event graph, or graph(path, 'MyFunction')
    begin = g.event('BeginPlay')                             # the existing override (enabled if it was a ghost) or new
    say = g.call('PrintString', InString='Door ready')       # pin literals as keywords
    g.chain(begin, say)                                      # exec wires: begin.then -> say.execute (and on)
    bpedit.add_variable('/Game/BP_Door', 'Health', 'float', 100, editable=True)
    hit = g.custom_event('TakeHit', Amount='float')
    hp, minus, set_hp = g.get('Health'), g.call('Subtract_DoubleDouble'), g.set_var('Health')
    g.link(hp, 'Health', minus, 'A'); g.link(hit, 'Amount', minus, 'B'); g.link(minus, 'ReturnValue', set_hp, 'Health')
    g.chain(hit, set_hp)
    print(g.text())                                          # nodes with ids, pins, literals and links
    bpedit.compile('/Game/BP_Door')                          # raises with the compiler's messages on errors

Nodes are addressed by id (g.text() / g.nodes() show them) or by a unique title; pins by name or display name,
ignoring case and spaces ('then', 'execute', 'True', 'ReturnValue', 'In String'). Types: bool, int, float, string,
name, text, vector, rotator, transform, a class (Actor, /Game/BP_X), object:X, class:X, struct:X, enum:X, array:T.
Nothing is saved: compile, then ueh.save(path) (ue.py save).
"""
import json

import unreal

EAL = unreal.EditorAssetLibrary
AUTO = -2147483648  # the plugin's "place it below the graph" position


class BlueprintError(RuntimeError):
    pass


def _lib():
    lib = getattr(unreal, "UETKBlueprintLibrary", None)
    if lib is None:
        raise BlueprintError("the UETKEditor plugin is not loaded: `ue.py plugins install`, then `ue.py build --restart`")
    return lib


def _call(name, *args):
    raw = getattr(_lib(), name)(*args)
    result = json.loads(raw)
    if not result.pop("ok", False):
        raise BlueprintError(result.get("error", raw))
    return result


def blueprint(target):
    """A Blueprint from a path or object."""
    if isinstance(target, unreal.Blueprint):
        return target
    obj = EAL.load_asset(target.split(".")[0]) if EAL.does_asset_exist(target.split(".")[0]) else None
    if not isinstance(obj, unreal.Blueprint):
        raise BlueprintError("no Blueprint at %s" % target)
    return obj


def _literal(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    return str(value)


class Node:
    def __init__(self, graph, data):
        self.graph = graph
        self.data = data
        self.id = data["id"]

    @property
    def title(self):
        return self.data.get("title", "")

    def pins(self):
        return self.data.get("pins", [])

    def set(self, pin, value):
        """Set an unconnected input pin's literal."""
        return self.graph.set(self, pin, value)

    def __repr__(self):
        return "<Node %s %r>" % (self.id, self.title)


class Graph:
    """One graph of a Blueprint. Every method edits through the plugin and returns Node objects."""

    def __init__(self, bp, name=""):
        self.bp = blueprint(bp)
        self.name = name
        self._auto = set()  # ids placed automatically, which chain() may lay out

    # ---------------------------------------------------------------- reading
    def nodes(self):
        return [Node(self, n) for n in _call("describe_graph", self.bp, self.name)["nodes"]]

    def node(self, ident):
        """A node by id or unique title (always freshly read, so positions and links are current)."""
        if isinstance(ident, Node):
            ident = ident.id
        for n in self.nodes():
            if n.id == ident:
                return n
        matches = [n for n in self.nodes() if n.title.lower().replace(" ", "") == str(ident).lower().replace(" ", "")]
        if len(matches) != 1:
            raise BlueprintError("%d nodes match %r in %s" % (len(matches), ident, self.name or "EventGraph"))
        return matches[0]

    def find(self, cls=None, title=None):
        """Nodes whose class or title contains the given text."""
        out = []
        for n in self.nodes():
            if cls and cls.lower() not in n.data["class"].lower():
                continue
            if title and title.lower() not in n.title.lower():
                continue
            out.append(n)
        return out

    def text(self):
        """The graph as text: one block per node with its id, title, literals and links."""
        lines = ["graph %s of %s" % (self.name or "EventGraph", self.bp.get_path_name().split(".")[0])]
        for n in self.nodes():
            d = n.data
            extra = "  [%s]" % d["function"] if "function" in d else ""
            state = "  (disabled)" if d.get("disabled") else ""
            lines.append("%s  %s  @(%d, %d)%s%s" % (n.id, d["title"], d["x"], d["y"], extra, state))
            for p in d["pins"]:
                links = p.get("links", [])
                if p["dir"] == "in" and not links and "default" not in p:
                    continue
                if p["dir"] == "out" and not links:
                    continue
                label = p["name"] + (" (%s)" % p["display"] if "display" in p else "")
                if p["dir"] == "in":
                    value = " <- " + ", ".join(links) if links else " = %s" % p["default"]
                    lines.append("    in  %s : %s%s" % (label, p["type"], value))
                else:
                    lines.append("    out %s : %s -> %s" % (label, p["type"], ", ".join(links)))
        return "\n".join(lines)

    # ---------------------------------------------------------------- adding
    def _add(self, kind, spec, x, y, literals=None):
        placed_auto = x is None or y is None
        result = _call("add_node", self.bp, self.name, kind, json.dumps(spec),
                       AUTO if x is None else int(x), AUTO if y is None else int(y))
        node = Node(self, result["node"])
        if placed_auto and not result.get("existing"):
            self._auto.add(node.id)
        for pin, value in (literals or {}).items():
            self.set(node, pin, value)
        return self.node(node.id) if literals else node

    def call(self, function, cls=None, x=None, y=None, **literals):
        """A function call: PrintString, Delay, K2_DestroyActor/DestroyActor, Add_DoubleDouble... cls names the
        owning class when it's not the Blueprint itself or a Kismet/GameplayStatics library."""
        spec = {"function": function}
        if cls:
            spec["class"] = cls
        return self._add("call", spec, x, y, literals)

    def event(self, name, x=None, y=None):
        """An overridable event (BeginPlay, Tick, ActorBeginOverlap, AnyDamage...): the existing one, or a new one."""
        return self._add("event", {"name": name}, x, y)

    def custom_event(self, name, x=None, y=None, **params):
        """A custom event with parameters as keywords: custom_event('TakeHit', Amount='float')."""
        return self._add("custom_event", {"name": name, "params": params}, x, y)

    def get(self, variable, cls=None, x=None, y=None):
        spec = {"variable": variable}
        if cls:
            spec["class"] = cls
        return self._add("get", spec, x, y)

    def set_var(self, variable, cls=None, x=None, y=None, **literals):
        """A variable Set node (named set_var so set() can stay the pin-literal setter)."""
        spec = {"variable": variable}
        if cls:
            spec["class"] = cls
        return self._add("set", spec, x, y, literals)

    def branch(self, x=None, y=None):
        return self._add("branch", {}, x, y)

    def sequence(self, x=None, y=None):
        return self._add("sequence", {}, x, y)

    def self_ref(self, x=None, y=None):
        return self._add("self", {}, x, y)

    def cast(self, cls, x=None, y=None):
        return self._add("cast", {"class": cls}, x, y)

    def macro(self, name, x=None, y=None):
        """A standard macro: ForEachLoop, ForLoop, WhileLoop, DoOnce, DoN, Gate, FlipFlop, IsValid..."""
        return self._add("macro", {"name": name}, x, y)

    def any_node(self, cls, x=None, y=None, **props):
        """Any graph node class, with properties (Unreal's text form, or asset paths) set before its pins are made:
        any_node('K2Node_EnhancedInputAction', InputAction='/Game/Input/IA_Jump'). The class's module must be loaded."""
        import uprop
        text = {k: (uprop.object_path(v) if isinstance(v, str) and v.startswith("/") else str(v))
                for k, v in props.items()}
        return self._add("node", {"class": cls, "props": text}, x, y)

    def input_action(self, action, x=None, y=None):
        """An Enhanced Input action event, by path or asset name: exec outputs Triggered, Started, Ongoing,
        Canceled and Completed, plus ActionValue (and its elapsed times)."""
        import inputs
        ia = inputs._find_action(action)
        return self.any_node("K2Node_EnhancedInputAction", x, y, InputAction=ia.get_path_name())

    def comment(self, text, x=0, y=0, width=400, height=200):
        return self._add("comment", {"text": text, "width": width, "height": height}, x, y)

    # ---------------------------------------------------------------- wiring
    def link(self, a, a_pin, b, b_pin):
        """Wire a.a_pin to b.b_pin (a conversion node is added when the types need one). An automatically placed
        exec node wired off an exec output moves to the right of it (lower for later outputs, e.g. a Branch's False)."""
        before = {n.id for n in self.nodes()}
        result = _call("connect", self.bp, self.name, self._id(a), a_pin, self._id(b), b_pin)
        if result.get("conversion_node"):
            self._auto.update({n.id for n in self.nodes()} - before)  # the schema's conversion node: tidy() places it
        src, dst = self.node(a), self.node(b)
        exec_outs = [p["name"] for p in src.pins() if p["dir"] == "out" and p["type"] == "exec"]
        from_pin = result["linked"].split(" -> ")[0].split(".", 1)[1]
        if dst.id in self._auto and from_pin in exec_outs:  # exec links only: data links don't move exec nodes
            self.move(dst, src.data["x"] + 400, src.data["y"] + 180 * exec_outs.index(from_pin))
            self._auto.discard(dst.id)
        return result

    def unlink(self, a, a_pin, b=None, b_pin=""):
        """Break one link, or every link of a.a_pin when b is None."""
        return _call("disconnect", self.bp, self.name, self._id(a), a_pin, self._id(b) if b else "", b_pin)

    def chain(self, *nodes):
        """Wire the exec flow through the nodes in order (each one's first exec output to the next one's input),
        and lay out the ones placed automatically to the right of their predecessor."""
        for prev, nxt in zip(nodes, nodes[1:]):
            prev, nxt = self.node(prev), self.node(nxt)
            out_pin = next((p["name"] for p in prev.pins() if p["dir"] == "out" and p["type"] == "exec"), None)
            in_pin = next((p["name"] for p in nxt.pins() if p["dir"] == "in" and p["type"] == "exec"), None)
            if not out_pin or not in_pin:
                raise BlueprintError("no exec pins between %s and %s" % (prev.id, nxt.id))
            self.link(prev, out_pin, nxt, in_pin)
            if nxt.id in self._auto:
                self.move(nxt, prev.data["x"] + 400, prev.data["y"])
                self._auto.discard(nxt.id)
        return nodes[-1]

    def tidy(self):
        """Move automatically placed data nodes (gets, math, conversions) to the left of the node that uses their
        output, stacked by input order, so the graph reads left to right. chain() already lays out exec nodes."""
        for _ in range(3):  # data nodes feeding data nodes settle over a few passes
            nodes = {n.id: n for n in self.nodes()}
            for node in nodes.values():
                if node.id not in self._auto or any(p["type"] == "exec" for p in node.pins()):
                    continue
                consumers = [link.split(".")[0] for p in node.pins() if p["dir"] == "out" for link in p.get("links", [])]
                if not consumers or consumers[0] not in nodes:
                    continue
                consumer = nodes[consumers[0]]
                inputs = [p for p in consumer.pins() if p["dir"] == "in" and p["type"] != "exec"]
                slot = next((i for i, p in enumerate(inputs) if any(l.startswith(node.id + ".") for l in p.get("links", []))), 0)
                x, y = consumer.data["x"] - 320, consumer.data["y"] + 60 + 110 * slot
                if (node.data["x"], node.data["y"]) != (x, y):
                    self.move(node, x, y)

    def set(self, node, pin, value):
        """Set an unconnected input pin's literal (numbers, bools, strings, 'x,y,z', enum names, asset paths)."""
        return _call("set_pin_default", self.bp, self.name, self._id(node), pin, _literal(value))

    def move(self, node, x, y):
        result = _call("move_node", self.bp, self.name, self._id(node), int(x), int(y))
        return result

    def remove(self, node):
        return _call("remove_node", self.bp, self.name, self._id(node))

    def _id(self, node):
        return node.id if isinstance(node, Node) else str(node)

    def __repr__(self):
        return "<Graph %s of %s>" % (self.name or "EventGraph", self.bp.get_name())


def graph(bp, name=""):
    """A Graph to edit: the event graph by default, or a function/macro graph by name."""
    return Graph(bp, name)


def graphs(bp):
    """[{name, kind, nodes}] of a Blueprint, plus its parent class."""
    return _call("list_graphs", blueprint(bp))


def add_variable(bp, name, type_, default=None, category="", editable=False, replicated=False, expose_on_spawn=False):
    """Add a member variable (types as in the module docstring)."""
    return _call("add_variable", blueprint(bp), name, type_, "" if default is None else _literal(default), category,
                 editable, replicated, expose_on_spawn)


def add_function(bp, name, inputs=None, outputs=None, pure=False):
    """Add a function graph with {name: type} inputs/outputs; returns its Graph (entry/result ids on .entry/.result)."""
    result = _call("add_function", blueprint(bp), name, json.dumps(inputs or {}), json.dumps(outputs or {}), pure)
    g = Graph(bp, result["graph"])
    g.entry, g.result = result["entry"], result["result"]
    return g


def compile(bp, raise_on_error=True):
    """Compile; returns {'status': ok|warnings|error, 'messages': [...]}, raising on errors by default."""
    result = _call("compile", blueprint(bp))
    if raise_on_error and result["status"] == "error":
        raise BlueprintError("%s failed to compile:\n  %s" % (
            blueprint(bp).get_name(), "\n  ".join("%s: %s" % (m["severity"], m["message"]) for m in result["messages"])))
    return result
