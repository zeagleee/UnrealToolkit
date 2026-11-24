"""ueh - Unreal Editor helpers for agent-driven editing. Runs inside UnrealEditor.

Pre-imported as `ueh` in every `ue.py py` call (the bridge reloads it when this file changes).
`ueh.help()` lists everything. Conventions:
  * "target" arguments accept an asset path (/Game/A/B or /Game/A/B.B), a loaded unreal.Object,
    or a level actor label. Blueprint asset paths resolve to the generated class's CDO for
    property access (props/get/set), and to the Blueprint itself everywhere else.
  * Rotations are (pitch, yaw, roll) tuples; locations are (x, y, z) tuples.
  * Nothing is saved unless a function says so (save=True or ueh.save()).
"""
import json
import os
import re
import tempfile

import unreal

EAL = unreal.EditorAssetLibrary
# [ \t]*, not \s*: \s* crossed the line break, so a property with no description swallowed the next property's line.
_PROP_DOC_RE = re.compile(r"^- ``(\w+)`` \((.*?)\):[ \t]*\[(Read-Write|Read-Only)\][ \t]*(.*)$", re.M)
_BLUEPRINT_CLASSES = ("Blueprint", "WidgetBlueprint", "AnimBlueprint", "EditorUtilityBlueprint",
                      "EditorUtilityWidgetBlueprint", "ControlRigBlueprint")


def _sub(cls):
    return unreal.get_editor_subsystem(cls)


def _registry():
    return unreal.AssetRegistryHelpers.get_asset_registry()


def _package(path):
    """'/Game/A/B.B' or '/Game/A/B' -> '/Game/A/B'."""
    return path.split(".", 1)[0] if path.startswith("/") else path


def _vec(v):
    return v if isinstance(v, unreal.Vector) else unreal.Vector(*v)


def _rot(r):
    return r if isinstance(r, unreal.Rotator) else unreal.Rotator(pitch=r[0], yaw=r[1], roll=r[2])


def _jsonable(value):
    import agent_bridge
    return agent_bridge.to_jsonable(value)


# --------------------------------------------------------------------------- discovery

def help(name=None):
    """List helpers, or print the full doc of one helper: ueh.help('set')."""
    import sys
    project = sys.modules.get("uetk_project_helpers")
    if name:
        fn = globals().get(name) or getattr(project, name, None)
        print(fn.__doc__ if fn else "no helper named %r" % name)
        return
    for key, fn in sorted(globals().items()):
        if callable(fn) and not key.startswith("_") and getattr(fn, "__module__", None) == __name__:
            doc = (fn.__doc__ or "").strip().splitlines()
            print("%-18s %s" % (key, doc[0] if doc else ""))
    if project is not None:
        print("\nProject helpers (%s; also as proj.<name>):" % project.__file__)
        for key, fn in sorted(vars(project).items()):
            if callable(fn) and not key.startswith("_") and getattr(fn, "__module__", None) == project.__name__:
                doc = (fn.__doc__ or "").strip().splitlines()
                print("%-18s %s" % (key, doc[0] if doc else ""))


def project_modules():
    """C++ module names declared in the .uproject."""
    with open(unreal.Paths.get_project_file_path(), "r", encoding="utf-8") as f:
        return [m["Name"] for m in json.load(f).get("Modules", [])]


def cls(name):
    """Resolve a class: unreal.Actor, 'RMRobot'/'ARMRobot', '/Script/Engine.Actor', or a Blueprint path."""
    if isinstance(name, unreal.Class):
        return name
    if isinstance(name, type) and hasattr(name, "static_class"):
        return name.static_class()
    if name.startswith("/Script/"):
        found = unreal.load_class(None, name)
    elif name.startswith("/"):
        # The generated class directly: EditorAssetLibrary refuses to run during PIE.
        package = _package(name)
        found = unreal.load_class(None, "%s.%s_C" % (package, package.rsplit("/", 1)[1]))
        if not found and not is_pie():
            found = EAL.load_blueprint_class(package)
    else:
        found = None
        candidates = [name] + ([name[1:]] if len(name) > 1 and name[0] in "AU" and name[1].isupper() else [])
        for module in project_modules() + ["Engine", "CoreUObject", "UMG", "EnhancedInput", "Niagara"]:
            for candidate in candidates:
                found = unreal.find_object(None, "/Script/%s.%s" % (module, candidate))  # silent on miss
                if found:
                    break
            if found:
                break
        if not found and hasattr(unreal, name) and hasattr(getattr(unreal, name), "static_class"):
            found = getattr(unreal, name).static_class()
    if not found:
        raise LookupError("class not found: %s" % name)
    return found


def find(path="/Game", cls=None, name=None, parent=None, recursive=True, limit=200):
    """Search the asset registry. cls: asset class name (e.g. 'Blueprint', 'StaticMesh');
    name: substring of asset name; parent: substring of a Blueprint's parent class."""
    results = []
    for data in _registry().get_assets_by_path(path, recursive=recursive):
        class_name = str(data.asset_class_path.asset_name)
        if cls and class_name.lower() != cls.lower():
            continue
        asset_name = str(data.asset_name)
        if name and name.lower() not in asset_name.lower():
            continue
        item = {"path": str(data.package_name), "class": class_name}
        parent_tag = str(data.get_tag_value("ParentClass") or "")
        if parent_tag and parent_tag != "None":
            item["parent"] = parent_tag
        if parent and parent.lower() not in parent_tag.lower():
            continue
        results.append(item)
        if len(results) >= limit:
            break
    return results


def info(path):
    """Asset summary: class, parent class, tags, dependency/referencer counts, dirty state."""
    data = EAL.find_asset_data(path)
    if not data.is_valid():
        raise LookupError("asset not found: %s" % path)
    package = str(data.package_name)
    result = {
        "path": package,
        "object_path": "%s.%s" % (package, data.asset_name),
        "class": str(data.asset_class_path.asset_name),
        "dependencies": len(deps(package)),
        "referencers": len(refs(package)),
        "dirty": package in dirty(),
    }
    for tag in ("ParentClass", "NativeParentClass", "GeneratedClass", "BlueprintType"):
        value = data.get_tag_value(tag)
        if value and str(value) != "None":
            result[tag] = str(value)
    return result


def _dep_options(hard_only):
    return unreal.AssetRegistryDependencyOptions(
        include_soft_package_references=not hard_only, include_hard_package_references=True,
        include_searchable_names=False, include_soft_management_references=False,
        include_hard_management_references=False)


def deps(path, hard_only=False):
    """Packages this asset depends on."""
    return [str(p) for p in (_registry().get_dependencies(_package(path), _dep_options(hard_only)) or [])]


def refs(path, hard_only=False):
    """Packages that reference this asset."""
    return [str(p) for p in (_registry().get_referencers(_package(path), _dep_options(hard_only)) or [])]


# --------------------------------------------------------------------------- loading & properties

def asset(path):
    """Load an asset (or return the object unchanged)."""
    if isinstance(path, unreal.Object):
        return path
    obj = EAL.load_asset(path) if EAL.does_asset_exist(_package(path)) else None
    if obj is None:
        obj = unreal.load_object(None, path)
    if obj is None:
        raise LookupError("asset not found: %s" % path)
    return obj


def resolve(target, cdo=True):
    """Object that property helpers operate on (BP asset -> its CDO when cdo=True; label -> actor)."""
    if isinstance(target, unreal.Object):
        obj = target
    elif target.startswith("/"):
        obj = asset(target)
    else:
        return actor(target)
    if cdo and isinstance(obj, unreal.Blueprint):
        return unreal.get_default_object(EAL.load_blueprint_class(obj.get_path_name().split(".")[0]))
    return obj


def prop_specs(target):
    """{property_name: (type, access, doc)} for every editor-visible property of the object's native type."""
    obj = resolve(target)
    specs = {}
    for klass in type(obj).__mro__:
        for m in _PROP_DOC_RE.finditer(klass.__dict__.get("__doc__") or ""):
            specs.setdefault(m.group(1), (m.group(2), m.group(3), m.group(4).strip()))
    return specs


def props(target, filter=None, types=False):
    """Current values of editor properties (native ones; use get() for Blueprint-added variables).
    filter: case-insensitive substring/regex on the name. types=True includes type and access."""
    obj = resolve(target)
    pattern = re.compile(filter, re.I) if filter else None
    out = {}
    for name, (ptype, access, _doc) in sorted(prop_specs(obj).items()):
        if pattern and not pattern.search(name):
            continue
        try:
            value = _jsonable(obj.get_editor_property(name))
        except Exception as exc:
            value = "<error: %s>" % exc
        out[name] = {"type": ptype, "access": access, "value": value} if types else value
    return out


def _walk(obj, path):
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if part.startswith("["):
            obj = obj[int(part[1:-1])]
        else:
            obj = obj.get_editor_property(part)
    return obj


def get(target, prop_path):
    """Read a property; dotted/indexed paths allowed: get('/Game/BP_X', 'mesh.static_mesh')."""
    return _walk(resolve(target), prop_path)


def set(target, prop_path, value, save=False):
    """Set a property (on the CDO for Blueprint assets) with editor change notifications.
    Nested paths set the final property on the owning sub-object; struct members are written back.
    save=True saves the owning package."""
    obj = resolve(target)
    parts = prop_path.split(".")
    owner = _walk(obj, ".".join(parts[:-1])) if len(parts) > 1 else obj
    notify = unreal.PropertyAccessChangeNotifyMode.ALWAYS
    if isinstance(owner, unreal.StructBase):
        owner.set_editor_property(parts[-1], value)
        parent = _walk(obj, ".".join(parts[:-2])) if len(parts) > 2 else obj
        parent.set_editor_property(parts[-2], owner, notify)
    else:
        owner.set_editor_property(parts[-1], value, notify)
    package = obj.get_outermost()
    package.mark_package_dirty() if hasattr(package, "mark_package_dirty") else obj.modify()
    if save:
        save_object(obj)
    return _jsonable(_walk(obj, prop_path))


def export_text(target, cdo=True):
    """T3D text of an object: every property incl. C++ UPROPERTYs and Blueprint variables (BP path -> CDO)."""
    obj = resolve(target, cdo=cdo)
    out_path = os.path.join(tempfile.gettempdir(), "ueh_export_%d.t3d" % os.getpid())
    task = unreal.AssetExportTask()
    task.set_editor_property("object", obj)
    task.set_editor_property("filename", out_path)
    task.set_editor_property("exporter", unreal.ObjectExporterT3D())
    task.set_editor_property("automated", True)
    task.set_editor_property("prompt", False)
    task.set_editor_property("replace_identical", True)
    if not unreal.Exporter.run_asset_export_task(task):
        raise RuntimeError("T3D export failed for %s" % obj.get_path_name())
    with open(out_path, "r", encoding="utf-8-sig", errors="replace") as f:
        text = f.read()
    os.remove(out_path)
    return text


def _export_t3d(obj, filename):
    task = unreal.AssetExportTask()
    task.set_editor_property("object", obj)
    task.set_editor_property("filename", filename)
    task.set_editor_property("exporter", unreal.ObjectExporterT3D())
    task.set_editor_property("automated", True)
    task.set_editor_property("prompt", False)
    task.set_editor_property("replace_identical", True)
    return unreal.Exporter.run_asset_export_task(task)


def export_blueprints(out_dir, roots=("/Game",), skip=()):
    """Export Blueprints, level Blueprints and data assets under `roots` to T3D/JSON in out_dir (for `ue.py bpdump`).
    Per Blueprint: <name>.t3d, its CDO (<name>.cdo.t3d) and its parent class's CDO (<name>.parent.t3d).
    Data assets, structs and enums -> <name>.t3d; data tables -> <name>.json. skip: package-path prefixes.
    Loads (and so compiles) every Blueprint; saves nothing."""
    os.makedirs(out_dir, exist_ok=True)
    reg = _registry()
    skip = tuple(skip)

    def assets_of(module, cls_name):
        flt = unreal.ARFilter(package_paths=list(roots), recursive_paths=True, recursive_classes=True,
                              class_paths=[unreal.TopLevelAssetPath(module, cls_name)])
        return [ad for ad in reg.get_assets(flt) if not str(ad.package_name).startswith(skip)]

    def safe_name(pkg):
        name = pkg.lstrip("/").replace("/", "__")
        return name[len("Game__"):] if name.startswith("Game__") else name

    report = {"blueprints": 0, "data": 0, "levels": 0, "errors": []}
    for ad in assets_of("/Script/Engine", "Blueprint"):
        pkg = str(ad.package_name)
        base = os.path.join(out_dir, safe_name(pkg))
        try:
            _export_t3d(EAL.load_asset(pkg), base + ".t3d")
            gen = EAL.load_blueprint_class(pkg)
            if gen:
                _export_t3d(unreal.get_default_object(gen), base + ".cdo.t3d")
            with open(base + ".t3d", encoding="utf-8-sig", errors="replace") as f:
                m = re.search(r"^\s*ParentClass=.*?'([^']+)'", f.read(), re.M)
            parent = unreal.load_class(None, m.group(1)) if m else None
            if parent:
                _export_t3d(unreal.get_default_object(parent), base + ".parent.t3d")
            report["blueprints"] += 1
        except Exception as exc:
            report["errors"].append([pkg, str(exc)])

    # UserDefinedStruct moved from Engine to CoreUObject in UE 5.7; asking for both covers either version.
    data_classes = [("/Script/Engine", "DataAsset"), ("/Script/CoreUObject", "UserDefinedStruct"),
                    ("/Script/Engine", "UserDefinedStruct"), ("/Script/Engine", "UserDefinedEnum"),
                    ("/Script/Engine", "DataTable")]
    done = {}  # (not set(): this module's own `set` helper shadows the builtin)
    for module, cls_name in data_classes:
        for ad in assets_of(module, cls_name):
            pkg = str(ad.package_name)
            if pkg in done:
                continue
            done[pkg] = True
            base = os.path.join(out_dir, safe_name(pkg))
            try:
                obj = EAL.load_asset(pkg)
                if isinstance(obj, unreal.DataTable):
                    with open(base + ".json", "w", encoding="utf-8") as f:
                        f.write(unreal.DataTableFunctionLibrary.export_data_table_to_json_string(obj))
                else:
                    _export_t3d(obj, base + ".t3d")
                report["data"] += 1
            except Exception as exc:
                report["errors"].append([pkg, str(exc)])

    # Level Blueprints exist only once someone has opened one; they live inside the map package.
    for ad in assets_of("/Script/Engine", "World"):
        pkg = str(ad.package_name)
        name = pkg.split("/")[-1]
        try:
            EAL.load_asset(pkg)
            lsb = unreal.find_object(None, "%s.%s:PersistentLevel.%s" % (pkg, name, name))
            if lsb:
                _export_t3d(lsb, os.path.join(out_dir, "LEVEL__" + safe_name(pkg) + ".t3d"))
                report["levels"] += 1
        except Exception as exc:
            report["errors"].append([pkg, str(exc)])
    return report


# --------------------------------------------------------------------------- saving

def dirty():
    """Package names with unsaved changes (maps + content)."""
    utils = unreal.EditorLoadingAndSavingUtils
    packages = list(utils.get_dirty_map_packages()) + list(utils.get_dirty_content_packages())
    return sorted({p.get_name() for p in packages})


def save_object(obj):
    """Save the package that owns an object."""
    package = obj.get_outermost()
    return unreal.EditorLoadingAndSavingUtils.save_packages([package], False)


def save(*paths):
    """Save the given assets (even if not dirty), or every dirty package when called with no args."""
    if not paths:
        before = dirty()
        ok = unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
        return {"ok": ok, "saved": before, "still_dirty": dirty()}
    return {p: EAL.save_asset(_package(p), only_if_is_dirty=False) for p in paths}


# --------------------------------------------------------------------------- asset operations

def duplicate(src, dst):
    """Duplicate an asset to a new path."""
    return EAL.duplicate_asset(_package(src), _package(dst))


def rename(src, dst):
    """Rename/move an asset (leaves a redirector; see fix_redirectors)."""
    return EAL.rename_asset(_package(src), _package(dst))


def delete(path):
    """Delete an asset. Refuses if something still references it unless forced via EAL directly."""
    referencers = [r for r in refs(path) if r != _package(path)]
    if referencers:
        raise RuntimeError("still referenced by: %s" % referencers)
    return EAL.delete_asset(_package(path))


def fix_redirectors(path="/Game"):
    """Fix up and remove redirectors under a folder."""
    redirectors = [d.get_asset() for d in _registry().get_assets_by_path(path, recursive=True)
                   if str(d.asset_class_path.asset_name) == "ObjectRedirector"]
    if redirectors:
        unreal.AssetToolsHelpers.get_asset_tools().fixup_referencers(redirectors)
    return len(redirectors)


def import_files(files, dest, replace=True, save=True):
    """Import source files (fbx/png/wav/...) into a content folder with the engine's defaults. Returns imported object
    paths. importer.run adds kinds, presets, folders and reimport (`ue.py import`)."""
    tasks = []
    for filename in ([files] if isinstance(files, str) else files):
        task = unreal.AssetImportTask()
        task.set_editor_property("filename", filename)
        task.set_editor_property("destination_path", dest)
        task.set_editor_property("automated", True)
        task.set_editor_property("replace_existing", replace)
        task.set_editor_property("save", save)
        tasks.append(task)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks(tasks)
    return [str(p) for t in tasks for p in t.get_editor_property("imported_object_paths")]


def create_bp(parent, dest, save=True):
    """Create a Blueprint (or Widget Blueprint for UserWidget parents) at dest from a parent class."""
    parent_class = cls(parent)
    folder, name = _package(dest).rsplit("/", 1)
    if unreal.MathLibrary.class_is_child_of(parent_class, unreal.UserWidget.static_class()):
        factory, asset_class = unreal.WidgetBlueprintFactory(), unreal.WidgetBlueprint
    else:
        factory, asset_class = unreal.BlueprintFactory(), unreal.Blueprint
    factory.set_editor_property("parent_class", parent_class)
    bp = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, asset_class, factory)
    if bp is None:
        raise RuntimeError("could not create %s" % dest)
    if save:
        save_object(bp)
    return bp


def create_asset(dest, asset_class, factory, save=True):
    """Generic asset creation, e.g. create_asset('/Game/Data/DA_X', unreal.DataAsset, unreal.DataAssetFactory())."""
    folder, name = _package(dest).rsplit("/", 1)
    obj = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, asset_class, factory)
    if obj is None:
        raise RuntimeError("could not create %s" % dest)
    if save:
        save_object(obj)
    return obj


# --------------------------------------------------------------------------- blueprints

def compile_bp(path):
    """Compile one Blueprint. Compiler errors/warnings show up in the call's log output."""
    bp = asset(path)
    unreal.BlueprintEditorLibrary.compile_blueprint(bp)
    return bp.get_path_name()


def compile_bps(path="/Game", skip=()):
    """Load and compile every Blueprint under a folder (skip: path prefixes). Returns the count."""
    count = 0
    for data in _registry().get_assets_by_path(path, recursive=True):
        package = str(data.package_name)
        if str(data.asset_class_path.asset_name) not in _BLUEPRINT_CLASSES or package.startswith(tuple(skip)):
            continue
        unreal.BlueprintEditorLibrary.compile_blueprint(EAL.load_asset(package))
        count += 1
    return count


def reparent_bp(path, new_parent, save=False):
    """Change a Blueprint's parent class."""
    bp = asset(path)
    unreal.BlueprintEditorLibrary.reparent_blueprint(bp, cls(new_parent))
    if save:
        save_object(bp)
    return bp.get_path_name()


# --------------------------------------------------------------------------- data tables

def dt_json(path):
    """Data table rows as parsed JSON."""
    return json.loads(unreal.DataTableFunctionLibrary.export_data_table_to_json_string(asset(path)))


def dt_fill(path, rows, save=False):
    """Replace a data table's rows from JSON text or a list of row dicts (each with a 'Name' key)."""
    table = asset(path)
    text = rows if isinstance(rows, str) else json.dumps(rows)
    ok = unreal.DataTableFunctionLibrary.fill_data_table_from_json_string(table, text)
    if ok and save:
        save_object(table)
    return ok


# --------------------------------------------------------------------------- levels & actors

def editor_world():
    """The editor (non-PIE) world."""
    return _sub(unreal.UnrealEditorSubsystem).get_editor_world()


def level():
    """Path of the level open in the editor."""
    return editor_world().get_outermost().get_name()


def open_level(path):
    """Open a level in the editor (fails if the current level has unsaved changes the editor wants to prompt about)."""
    return _sub(unreal.LevelEditorSubsystem).load_level(_package(path))


def save_level():
    """Save the current level."""
    return _sub(unreal.LevelEditorSubsystem).save_current_level()


def _actor_summary(a):
    loc, rot = a.get_actor_location(), a.get_actor_rotation()
    return {
        "label": a.get_actor_label(), "name": a.get_name(), "class": a.get_class().get_name(),
        "location": [round(loc.x, 2), round(loc.y, 2), round(loc.z, 2)],
        "rotation": [round(rot.pitch, 2), round(rot.yaw, 2), round(rot.roll, 2)],
        "folder": str(a.get_folder_path()),
    }


def actors(cls=None, name=None, limit=500):
    """Actors in the editor level. cls: substring of class name; name: substring of label/name."""
    out = []
    for a in _sub(unreal.EditorActorSubsystem).get_all_level_actors():
        if cls and cls.lower() not in a.get_class().get_name().lower():
            continue
        if name and name.lower() not in (a.get_actor_label() + "|" + a.get_name()).lower():
            continue
        out.append(_actor_summary(a))
        if len(out) >= limit:
            break
    return out


def actor(label):
    """Find a level actor by exact label, then by object name, then by unique label substring."""
    all_actors = _sub(unreal.EditorActorSubsystem).get_all_level_actors()
    for key in (lambda a: a.get_actor_label() == label, lambda a: a.get_name() == label):
        for a in all_actors:
            if key(a):
                return a
    partial = [a for a in all_actors if label.lower() in a.get_actor_label().lower()]
    if len(partial) == 1:
        return partial[0]
    raise LookupError("no unique actor '%s' (%d partial matches)" % (label, len(partial)))


def spawn(class_or_path, location=(0, 0, 0), rotation=(0, 0, 0), label=None):
    """Spawn an actor in the editor level from a class or Blueprint path."""
    a = _sub(unreal.EditorActorSubsystem).spawn_actor_from_class(cls(class_or_path), _vec(location), _rot(rotation))
    if a is None:
        raise RuntimeError("spawn failed")
    if label:
        a.set_actor_label(label)
    return _actor_summary(a)


def move(label, location=None, rotation=None):
    """Move/rotate a level actor."""
    a = actor(label)
    if location is not None:
        a.set_actor_location(_vec(location), False, False)
    if rotation is not None:
        a.set_actor_rotation(_rot(rotation), False)
    return _actor_summary(a)


def destroy(*labels):
    """Delete level actors by label."""
    sub = _sub(unreal.EditorActorSubsystem)
    return {label: sub.destroy_actor(actor(label)) for label in labels}


def select(*labels):
    """Select level actors by label (no args clears the selection)."""
    _sub(unreal.EditorActorSubsystem).set_selected_level_actors([actor(l) for l in labels])
    return list(labels)


def components(label):
    """Components of a level actor: name, class, and attach parent."""
    out = []
    for c in actor(label).get_components_by_class(unreal.ActorComponent):
        parent = c.get_attach_parent() if isinstance(c, unreal.SceneComponent) else None
        out.append({"name": c.get_name(), "class": c.get_class().get_name(),
                    "parent": parent.get_name() if parent else None})
    return out


def camera():
    """Editor viewport camera (location, rotation)."""
    info = _sub(unreal.UnrealEditorSubsystem).get_level_viewport_camera_info()
    loc, rot = info[-2], info[-1]
    return {"location": [loc.x, loc.y, loc.z], "rotation": [rot.pitch, rot.yaw, rot.roll]}


def set_camera(location, rotation=(0, 0, 0)):
    """Move the editor viewport camera."""
    _sub(unreal.UnrealEditorSubsystem).set_level_viewport_camera_info(_vec(location), _rot(rotation))
    return camera()


def focus(label, distance=None):
    """Point the editor viewport camera at an actor from a distance scaled to its bounds."""
    a = actor(label)
    origin, extent = a.get_actor_bounds(False)
    dist = distance or max(extent.length() * 2.5, 200.0)
    eye = origin + unreal.Vector(-dist * 0.7, -dist * 0.7, dist * 0.5)
    look = unreal.MathLibrary.find_look_at_rotation(eye, origin)
    return set_camera(eye, look)


# --------------------------------------------------------------------------- offscreen renders (UETKEditor plugin)
# PNGs of a camera view, an actor, an asset or a widget, rendered offscreen: the editor needn't be in front, the
# viewport camera doesn't move, and the level isn't changed. Default output: Saved/UnrealToolkit/renders/.

def _render_lib():
    lib = getattr(unreal, "UETKEditorLibrary", None)
    if lib is None:
        raise RuntimeError("the UETKEditor plugin is not loaded: `ue.py plugins install`, then `ue.py build --restart`")
    return lib


def _render_out(out, name):
    if out:
        return out
    safe = re.sub(r"[^\w.-]+", "_", name).strip("_") or "render"
    return os.path.join(unreal.Paths.project_saved_dir(), "UnrealToolkit", "renders", safe + ".png")


def _render_settings(size=(1280, 720), fov=60.0, exposure=None, yaws=None, pitch=-20.0, zoom=1.0, columns=0,
                     labels=True, isolate=False, floor=True, environment=True, mesh=None, post=None):
    s = unreal.UETKRenderSettings()
    s.set_editor_property("width", int(size[0]))
    s.set_editor_property("height", int(size[1]))
    s.set_editor_property("fov", float(fov))
    # exposure: None = meter each frame to mid-gray; a number = fixed exposure compensation (stops).
    s.set_editor_property("auto_exposure", exposure is None)
    s.set_editor_property("exposure_bias", 0.0 if exposure is None else float(exposure))
    s.set_editor_property("yaws", [float(y) for y in (yaws or [])])
    s.set_editor_property("pitch", float(pitch))
    s.set_editor_property("zoom", float(zoom))
    s.set_editor_property("columns", int(columns))
    s.set_editor_property("labels", bool(labels))
    s.set_editor_property("isolate", bool(isolate))
    s.set_editor_property("show_floor", bool(floor))
    s.set_editor_property("show_environment", bool(environment))
    if mesh:
        s.set_editor_property("preview_mesh", asset(mesh))
    if post:
        s.set_editor_property("post_process_materials", [asset(m) for m in ([post] if isinstance(post, str) else post)])
    return s


def _render_result(raw):
    result = json.loads(raw)
    if not result.pop("ok", False):
        raise RuntimeError(result.get("error", raw))
    return result


def render_view(location=None, rotation=None, look_at=None, out=None, size=(1600, 900), fov=90.0, exposure=None,
                world=None, post=None):
    """Render a camera view of the editor level (or `world`) to PNG. Default camera: the level viewport's.
    look_at=(x, y, z) aims the camera; exposure=None meters to mid-gray, a number fixes it (stops).
    post: post-process material path(s) to apply (an outline, a colour grade)."""
    if location is None:
        cam = camera()
        location = cam["location"]
        rotation = rotation if rotation is not None else cam["rotation"]
    if look_at is not None:
        rot = unreal.MathLibrary.find_look_at_rotation(_vec(location), _vec(look_at))
        rotation = (rot.pitch, rot.yaw, rot.roll)
    rotation = rotation if rotation is not None else (0, 0, 0)
    name = "view_%d_%d_%d" % tuple(int(round(v)) for v in location)
    raw = _render_lib().render_view(world, _render_out(out, name), _vec(location), _rot(rotation),
                                    _render_settings(size=size, fov=fov, exposure=exposure, post=post))
    return _render_result(raw)


def render_pie(out=None, player_index=0, size=(1600, 900), exposure=None, post=None):
    """Render what a PIE player's camera sees (its location, rotation and FOV), offscreen. The game keeps running;
    HUD/UMG is not included (render_widget(..., pie=True) draws those)."""
    world = pie_world()
    if world is None:
        raise RuntimeError("PIE is not running")
    controller = unreal.GameplayStatics.get_player_controller(world, player_index)
    manager = controller.player_camera_manager if controller else None
    if manager is None:
        raise RuntimeError("player %d has no camera manager" % player_index)
    loc, rot = manager.get_camera_location(), manager.get_camera_rotation()
    settings = _render_settings(size=size, fov=manager.get_fov_angle(), exposure=exposure, post=post)
    # First-person arms/weapons (primitives tagged FirstPerson, 5.6+) use the camera's own FOV and scale.
    target = controller.get_view_target()
    cameras = [c for c in (target.get_components_by_class(unreal.CameraComponent) if target else []) if c.is_active()]
    if cameras:
        for camera_prop, setting in (("enable_first_person_field_of_view", "use_first_person_field_of_view"),
                                     ("first_person_field_of_view", "first_person_field_of_view"),
                                     ("enable_first_person_scale", "use_first_person_scale"),
                                     ("first_person_scale", "first_person_scale")):
            try:
                settings.set_editor_property(setting, cameras[0].get_editor_property(camera_prop))
            except Exception:
                pass  # engines before 5.6 have no first-person camera settings
    # The player's own body (owner-no-see, or a 5.6+ world-space representation) would draw from inside the camera.
    hidden = []
    for prim in (target.get_components_by_class(unreal.PrimitiveComponent) if target else []):
        own_body = False
        try:
            own_body = prim.get_editor_property("owner_no_see")
            own_body = own_body or "WORLD_SPACE" in str(prim.get_editor_property("first_person_primitive_type"))
        except Exception:
            pass
        if own_body:
            hidden.append(prim)
    settings.set_editor_property("hidden_components", hidden)
    import agent_bridge
    instance = agent_bridge.instance_name()  # game processes of `ue.py net` would overwrite each other's file
    name = ("%s_" % instance if instance else "") + "pie_player%d" % player_index
    raw = _render_lib().render_view(world, _render_out(out, name), loc, rot, settings)
    return _render_result(raw)


def render_actor(label, out=None, yaws=None, pitch=-20.0, isolate=False, zoom=1.0, size=(800, 600), fov=50.0,
                 exposure=None, columns=0, post=None):
    """Render a level actor from several angles (yaws relative to its facing; default four 3/4 views), framed to
    its bounds. isolate=True draws only the actor. Works on PIE actors too (pass the object)."""
    target = label if isinstance(label, unreal.Actor) else actor(label)
    settings = _render_settings(size=size, fov=fov, exposure=exposure, yaws=yaws, pitch=pitch, zoom=zoom,
                                columns=columns, isolate=isolate, post=post)
    return _render_result(_render_lib().render_actor(target, _render_out(out, "actor_" + target.get_actor_label()),
                                                     settings))


def render_asset(path, out=None, yaws=None, pitch=-20.0, zoom=1.0, size=(800, 600), fov=50.0, exposure=None,
                 floor=True, environment=True, columns=0, mesh=None, post=None):
    """Render an asset in a lit preview scene from several angles: static/skeletal mesh, material (a surface
    material on `mesh`, a static mesh path, default a sphere; a post-process material over a sphere and a cube),
    Blueprint actor, widget Blueprint (drawn at size), texture (its image). Niagara systems go to vfx.preview.
    post: post-process material path(s) applied on top."""
    obj = asset(path)
    if isinstance(obj, unreal.NiagaraSystem):
        import vfx
        return vfx.preview(obj, out=out)
    settings = _render_settings(size=size, fov=fov, exposure=exposure, yaws=yaws, pitch=pitch, zoom=zoom,
                                columns=columns, floor=floor, environment=environment, mesh=mesh, post=post)
    return _render_result(_render_lib().render_asset(obj, _render_out(out, "asset_" + obj.get_name()), settings))


def render_widget(target, out=None, size=(1920, 1080), background=(0.02, 0.02, 0.02), pie=False, world=None):
    """Render a UMG widget to PNG at a screen size (with the project's DPI scale): a Widget Blueprint path, a
    widget class or instance. pie=True draws the live instance on screen in PIE (its real state); otherwise a
    fresh one is created in the editor level, so anything it reads from the game is at its defaults."""
    lib = _render_lib()
    widget = target
    if pie:
        game = pie_world()
        if game is None:
            raise RuntimeError("PIE is not running")
        wanted = cls(target) if not isinstance(target, unreal.UserWidget) else target.get_class()
        live = [w for w in unreal.ObjectIterator(unreal.UserWidget)
                if w.get_world() == game and w.is_in_viewport() and unreal.MathLibrary.class_is_child_of(w.get_class(), wanted)]
        if not live:
            raise RuntimeError("no %s on screen in PIE" % wanted.get_name())
        widget = live[0]
    elif isinstance(target, str):
        widget = asset(target) if target.startswith("/") else cls(target)
    name = "widget_" + (widget.get_name() if hasattr(widget, "get_name") else str(target))
    r, g, b = background[:3]
    raw = lib.render_widget(widget, _render_out(out, name), int(size[0]), int(size[1]), world,
                            unreal.LinearColor(r, g, b, 1))
    return _render_result(raw)


# --------------------------------------------------------------------------- play in editor

def is_pie():
    """True while a Play-In-Editor session is running (always False in a game process)."""
    return is_editor() and _sub(unreal.LevelEditorSubsystem).is_in_play_in_editor()


def pie_start(simulate=False):
    """Request Play-In-Editor (or Simulate). Starts on a later frame; poll is_pie()."""
    les = _sub(unreal.LevelEditorSubsystem)
    les.editor_play_simulate() if simulate else les.editor_request_begin_play()


def pie_stop():
    """Request the end of the PIE session."""
    _sub(unreal.LevelEditorSubsystem).editor_request_end_play()


def is_editor():
    """True in the editor; False in a game process (`ue.py net` instances run UnrealEditor.exe -game/-server)."""
    try:
        return _sub(unreal.UnrealEditorSubsystem) is not None
    except Exception:
        return False


def pie_world():
    """The running PIE world (None when not playing). In a game process: the game world."""
    if not is_editor():
        return game_world()
    return _sub(unreal.UnrealEditorSubsystem).get_game_world()


def game_world():
    """The world the game runs in: PIE's in the editor, the game's in a game process (None if there's none)."""
    if is_editor():
        return _sub(unreal.UnrealEditorSubsystem).get_game_world()
    for state in unreal.ObjectIterator(unreal.GameStateBase):
        if state.get_name().startswith("Default__"):
            continue  # class defaults have no world
        world = state.get_world()
        if world is not None:
            return world
    return None


def player(index=0):
    """(player_controller, pawn) of a local player in the game world (PIE, or a game process)."""
    world = game_world()
    if world is None:
        raise RuntimeError("no game world (PIE is not running)")
    return (unreal.GameplayStatics.get_player_controller(world, index),
            unreal.GameplayStatics.get_player_pawn(world, index))


def console(command, pie=False):
    """Run a console command in the editor world, or in the game (PIE or a game process) via player 0 so cheat
    Exec functions work. In a game process it always runs in the game."""
    if pie or not is_editor():
        world = game_world()
        controller = unreal.GameplayStatics.get_player_controller(world, 0) if world else None
        unreal.SystemLibrary.execute_console_command(world, command, controller)
    else:
        unreal.SystemLibrary.execute_console_command(editor_world(), command)
    return command


# --------------------------------------------------------------------------- real input (Enhanced Input forcing)
# Enhanced Input's cheat commands Input.+key/-key feed a key (or axis value) into the player's input every frame,
# through the active mapping contexts: Hold/Tap triggers, chords and modifiers (Negate, sensitivity) run as they
# would for a real keyboard and mouse, without touching the OS input (which would land in the user's window).

_held = {}  # key -> value text, forced until released


def key_down(key, value=None):
    """Start forcing a key: 'E', 'LeftMouseButton', 'SpaceBar', 'Gamepad_RightTrigger'. value: an axis value
    (0.4, or 'X=5 Y=-3' for Mouse2D/2D axes). Stays down until key_up/release_all."""
    text = "" if value is None else " %s" % value
    console("Input.+key %s%s" % (key, text), pie=True)
    _held[key] = text
    return key


def key_up(key):
    console("Input.-key %s" % key, pie=True)
    _held.pop(key, None)
    return key


def release_all():
    """Stop forcing every key these helpers pressed."""
    for key in list(_held):
        key_up(key)
    return []


def press(key, hold=0.08, value=None):
    """Scenario: `yield from ueh.press('R', 0.6)` holds a key for `hold` seconds of game time, then releases it
    (also when the scenario is cancelled). The default 0.08 s is a tap."""
    key_down(key, value)
    try:
        yield _input_wait(hold)
    finally:
        key_up(key)


def _input_wait(seconds):
    """`seconds` of game time, or of wall time while the game is paused: game time stands still in a pause menu,
    and a key can pause the game mid-hold (P opening the menu), so both clocks are watched."""
    import time
    world = game_world()
    start_game, start_real = game_time() or 0.0, time.time()

    def done():
        if world is None:
            return time.time() - start_real >= seconds
        if unreal.GameplayStatics.is_game_paused(world):
            return time.time() - start_real >= seconds
        return (game_time() or 0.0) - start_game >= seconds

    return until(done, timeout=seconds * 20 + 30, message="hold of %ss" % seconds)


def mouse(dx, dy, seconds=0.25):
    """Scenario: `yield from ueh.mouse(5, -3, 0.5)` moves the mouse by (dx, dy) per frame for `seconds`, through the
    mappings (Mouse XY 2D-Axis, e.g. IA_Look with its Negate and sensitivity modifiers)."""
    yield from press("Mouse2D", seconds, "X=%s Y=%s" % (float(dx), float(dy)))


def action(name, value=True, seconds=None):
    """Force an input action's value each frame (skips the mappings' triggers/modifiers, unlike key_down). With
    seconds, a scenario generator that releases it after that much game time; without, stays until -action."""
    console("Input.+action %s %s" % (name, value if not isinstance(value, bool) else str(value).lower()), pie=True)
    if seconds is None:
        return name

    def release():
        try:
            yield _input_wait(seconds)
        finally:
            console("Input.-action %s" % name, pie=True)
    return release()


def net_info():
    """This process's network role and session: {role, map, players: [names], time}. Role is standalone,
    listen_server, dedicated_server or client (a client that hasn't connected yet reports standalone)."""
    world = game_world()
    if world is None:
        return {"role": "none", "players": []}
    sys_lib = unreal.SystemLibrary
    if sys_lib.is_dedicated_server(world):
        role = "dedicated_server"
    elif sys_lib.is_server(world):
        role = "standalone" if sys_lib.is_standalone(world) else "listen_server"
    else:
        role = "client"
    state = unreal.GameplayStatics.get_game_state(world)
    players = [ps.get_player_name() for ps in (state.player_array if state else [])]
    return {"role": role, "map": world.get_outermost().get_name(), "players": players,
            "time": round(unreal.GameplayStatics.get_time_seconds(world), 2)}


# --------------------------------------------------------------------------- scenarios (waits)
# A cell with a top-level `yield` runs as a scenario: the game keeps ticking while it waits (scenario.py).

def _scenario():
    import scenario
    return scenario


def game_time():
    """Seconds of game time in the PIE world (None when not playing). Scenario waits count this."""
    world = pie_world()
    return unreal.GameplayStatics.get_time_seconds(world) if world else None


def wait(seconds):
    """Scenario: `yield ueh.wait(5)` resumes after 5 s of PIE game time (wall clock outside PIE); `yield 5` is the same."""
    return _scenario().Seconds(seconds, game_time)


def wait_real(seconds):
    """Scenario: `yield ueh.wait_real(5)` resumes after 5 wall-clock seconds."""
    return _scenario().Seconds(seconds, None)


def frames(count=1):
    """Scenario: `yield ueh.frames(3)` resumes after 3 editor ticks (`yield` alone = 1)."""
    return _scenario().Frames(count)


def until(predicate, timeout=60, message=None, interval=0):
    """Scenario: `value = yield ueh.until(lambda: ..., timeout=60)` resumes once predicate() is truthy and gets its value.

    The predicate runs every tick (or every `interval` wall-clock seconds); after `timeout` wall-clock seconds a
    TimeoutError is raised inside the scenario (catch it to carry on). `message` names the condition in that error."""
    return _scenario().Until(predicate, timeout=timeout, message=message, interval=interval)
