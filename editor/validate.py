"""validate - project checks for scripts, commit hooks and CI (`ue.py validate`). Pre-imported as `validate`.

    validate.run()                                         # the default checks on /Game
    validate.run(['/Game/UI'], checks=['data', 'blueprints'])
    print(validate.report(validate.run()))                 # the text `ue.py validate` prints
    validate.fix_redirectors('/Game')                      # resave referencers, delete the redirectors

run() returns {'findings': [...], 'counts': {'error': n, 'warning': n, 'info': n}, 'failed': bool,
'checked': {check: how many}, 'skipped': {check: why}, 'seconds': {check: s}}. A finding is
{'severity': 'error'|'warning'|'info', 'check', 'asset', 'message'}.

Checks (* = on by default):
  references*  references to packages that don't exist. Hard references are errors (the asset loads with the
               reference cleared), soft ones warnings.
  redirectors* leftover redirectors (warnings). fix_redirectors() fixes them (UETKEditor plugin).
  textures*    textures over max_texture_size on a side (default 4096; virtual textures exempt), and textures whose
               sides aren't powers of two outside the UI group (they get no mips and don't stream; importing
               sets such a texture to "no mipmaps" itself).
  blueprints*  compiles every Blueprint (Widget, Anim, Editor Utility...): the compiler's errors and warnings
               (UETKEditor plugin).
  data*        Data Validation, as the Content Browser's Validate Assets runs it: the engine's and the project's
               validators and each asset's IsDataValid (UETKEditor plugin). Loads every asset it checks.
  maps*        map check on `maps` (paths, or 'all' for every map under the roots), else the level open in the
               editor (UETKEditor plugin). In an editor window the open level comes back afterwards.
  unused       assets nothing references: info, a lead to follow. Maps, Editor Utilities and assets whose path
               appears under Config/ or Source/ aren't reported; code that builds paths at runtime isn't seen.
  naming       name prefixes by asset class (warnings): `naming: true` (or asking for the check by name) for
               NAMING below, or {class: prefix(es)}.

Settings come from <Project>/.uetk/validate.json, and keyword arguments to run() override them:
  {"roots": ["/Game"], "exclude": ["/Game/Developers"], "checks": {"unused": true, "data": false},
   "max_texture_size": 4096, "maps": ["/Game/Maps/Main"], "naming": true,
   "ignore": [{"check": "textures", "asset": "/Game/Sky/*"}, {"message": "*deprecated*"}],
   "fail_on": "error"}
`checks` may also be a list (exactly those). `ignore` entries match findings by fnmatch patterns on any of check,
asset, message and severity. fail_on: "error" (default) or "warning".
"""
import fnmatch
import json
import os
import re
import time

import unreal

CHECKS = ("references", "redirectors", "textures", "naming", "unused", "blueprints", "data", "maps")
DEFAULT_CHECKS = ("references", "redirectors", "textures", "blueprints", "data", "maps")
SEVERITIES = ("error", "warning", "info")

# Common Unreal naming conventions, used when a project turns naming on with `true`.
NAMING = {
    "Blueprint": ("BP_", "BPI_", "BPFL_", "BPML_", "B_"),
    "WidgetBlueprint": ("WBP_", "W_"),
    "AnimBlueprint": ("ABP_",),
    "EditorUtilityWidgetBlueprint": ("EUW_",),
    "EditorUtilityBlueprint": ("EUB_", "EUBP_"),
    "Material": ("M_",),
    "MaterialInstanceConstant": ("MI_", "MIC_"),
    "MaterialFunction": ("MF_",),
    "MaterialParameterCollection": ("MPC_",),
    "PhysicalMaterial": ("PM_",),
    "Texture2D": ("T_",),
    "TextureCube": ("T_", "HDR_"),
    "TextureRenderTarget2D": ("RT_",),
    "StaticMesh": ("SM_",),
    "SkeletalMesh": ("SK_", "SKM_"),
    "Skeleton": ("SK_", "SKEL_"),
    "PhysicsAsset": ("PA_", "PHYS_"),
    "AnimMontage": ("AM_",),
    "BlendSpace": ("BS_",),
    "BlendSpace1D": ("BS_",),
    "NiagaraSystem": ("NS_",),
    "NiagaraEmitter": ("NE_",),
    "SoundCue": ("SC_",),
    "SoundWave": ("S_", "SW_"),
    "DataTable": ("DT_",),
    "CurveTable": ("CT_",),
    "UserDefinedEnum": ("E_",),
    "UserDefinedStruct": ("F_", "S_", "ST_"),
    "InputAction": ("IA_",),
    "InputMappingContext": ("IMC_",),
    "BehaviorTree": ("BT_",),
    "BlackboardData": ("BB_",),
    "LevelSequence": ("LS_", "SEQ_"),
}

_BLUEPRINTS = ("Blueprint", "WidgetBlueprint", "AnimBlueprint", "EditorUtilityBlueprint", "EditorUtilityWidgetBlueprint",
               "ControlRigBlueprint")
_NEVER_UNUSED = ("World", "ObjectRedirector", "EditorUtilityBlueprint", "EditorUtilityWidgetBlueprint",
                 "PrimaryAssetLabel", "WorldDataLayers", "ActorFolder")
_SKIP_DEPENDENCIES = ("/Script/", "/Temp/", "/Memory/", "/Engine/Transient")
_PATH_RE = re.compile(r"/[A-Za-z0-9_]+(?:/[A-Za-z0-9_\-. ]+)+")


class ValidateError(RuntimeError):
    pass


# --------------------------------------------------------------------------- settings and asset lists

def _registry():
    return unreal.AssetRegistryHelpers.get_asset_registry()


def _project_dir():
    return os.path.abspath(unreal.Paths.project_dir())


def settings(**overrides):
    """The project's .uetk/validate.json merged with keyword overrides (None values ignored)."""
    path = os.path.join(_project_dir(), ".uetk", "validate.json")
    out = {"roots": ["/Game"], "exclude": [], "checks": None, "max_texture_size": 4096, "maps": None,
           "naming": None, "ignore": [], "fail_on": "error"}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            out.update(json.load(f))
    out.update({k: v for k, v in overrides.items() if v is not None})
    for key in ("roots", "exclude"):
        if isinstance(out[key], str):
            out[key] = [out[key]]
        out[key] = [p.rstrip("/") for p in out[key]]
    return out


def _checks(value, skip=(), naming=False):
    """Checks to run from a list (exactly those), a {name: on/off} dict over the defaults, or None (defaults).
    naming joins the defaults when the project sets naming rules."""
    defaults = DEFAULT_CHECKS + (("naming",) if naming else ())
    if value in (None, "default"):
        chosen = list(defaults)
    elif value == "all":
        chosen = list(CHECKS)
    elif isinstance(value, dict):
        chosen = [c for c in CHECKS if value.get(c, c in defaults)]
    else:
        chosen = [value] if isinstance(value, str) else list(value)
    unknown = [c for c in list(chosen) + list(skip) if c not in CHECKS]
    if unknown:
        raise ValidateError("unknown check %s (checks: %s)" % (", ".join(unknown), ", ".join(CHECKS)))
    return [c for c in CHECKS if c in chosen and c not in skip]


_scanned = []


def _wait_for_registry():
    """The registry with every mounted path scanned. A commandlet (headless) run only scans on request, and then
    references into plugin content (/Niagara/...) would all look broken."""
    registry = _registry()
    if _headless() and not _scanned:
        registry.search_all_assets(True)
        _scanned.append(True)
    elif registry.is_loading_assets():
        registry.wait_for_completion()
    return registry


def _external(package):
    """World Partition's one-file-per-actor packages, checked with their map."""
    return "/__External" in package


def _assets(cfg):
    """{package name: AssetData} under the roots, minus excluded folders."""
    registry = _wait_for_registry()
    out = {}
    for root in cfg["roots"]:
        for data in registry.get_assets_by_path(root, recursive=True, include_only_on_disk_assets=True):
            package = str(data.package_name)
            if any(package == ex or package.startswith(ex + "/") for ex in cfg["exclude"]):
                continue
            out.setdefault(package, data)
    return out


def _refresh(cfg):
    """Right after a save the registry still has a package's old dependencies, so a script that just saved assets
    would get false broken references and unused assets. Rescan what changed under each root's mount (/Game):
    about 0.1 s per 1000 packages. A headless run scans everything fresh anyway."""
    if _headless():
        return
    registry = _registry()
    for mount in sorted({"/" + r.strip("/").split("/")[0] for r in cfg["roots"]}):
        packages = sorted({str(d.package_name) for d in registry.get_assets_by_path(
            mount, recursive=True, include_only_on_disk_assets=True)})
        if packages:
            registry.scan_modified_asset_files(packages)


def _class(data):
    return str(data.asset_class_path.asset_name)


def _tag(data, name):
    try:
        value = data.get_tag_value(name)
    except Exception:
        return None
    if isinstance(value, tuple):  # (found, value) on some versions
        value = value[1] if value[0] else None
    return None if value in (None, "") else str(value)


def _dep_options(hard, soft, management=False):
    options = unreal.AssetRegistryDependencyOptions()
    for key, value in (("include_hard_package_references", hard), ("include_soft_package_references", soft),
                       ("include_game_package_references", True), ("include_editor_only_package_references", True),
                       ("include_searchable_names", False), ("include_hard_management_references", management),
                       ("include_soft_management_references", management)):
        try:
            options.set_editor_property(key, value)
        except Exception:  # 5.6 has no game / editor-only switches (it includes both)
            pass
    return options


def _lib():
    lib = getattr(unreal, "UETKValidateLibrary", None)
    if lib is None:
        raise ValidateError("needs the UETKEditor plugin (ue.py plugins install, then rebuild)")
    return lib


def _call(name, *args):
    raw = getattr(_lib(), name)(*args)
    result = json.loads(raw)
    if not result.pop("ok", False):
        raise ValidateError(result.get("error", raw))
    return result


def _finding(severity, check, asset, message):
    return {"severity": severity, "check": check, "asset": asset, "message": message.strip()}


# --------------------------------------------------------------------------- checks
# Each takes (assets, cfg) and returns (findings, how many it checked).

def check_references(assets, cfg):
    registry = _registry()
    hard, soft = _dep_options(True, False), _dep_options(False, True)
    exists = {}

    def missing(dep):
        if dep.startswith(_SKIP_DEPENDENCIES):
            return False
        if dep not in exists:
            exists[dep] = dep in assets or bool(registry.get_assets_by_package_name(dep))
        return not exists[dep]

    findings = []
    for package in sorted(assets):
        hard_deps = [str(d) for d in (registry.get_dependencies(package, hard) or [])]
        for dep in hard_deps:
            if missing(dep):
                findings.append(_finding("error", "references", package, "references %s, which doesn't exist" % dep))
        for dep in (str(d) for d in (registry.get_dependencies(package, soft) or [])):
            if dep not in hard_deps and missing(dep):
                findings.append(_finding("warning", "references", package,
                                         "soft reference to %s, which doesn't exist" % dep))
    return findings, len(assets)


def check_redirectors(assets, cfg):
    findings, count = [], 0
    for package, data in sorted(assets.items()):
        if _class(data) != "ObjectRedirector":
            continue
        count += 1
        target = _tag(data, "DestinationObject")
        findings.append(_finding("warning", "redirectors", package, "redirector%s; fix with `ue.py validate "
                                 "--fix-redirectors` or the Content Browser's Fix Up" % (" to " + target if target else "")))
    return findings, count


def _power_of_two(n):
    return n > 0 and n & (n - 1) == 0


def check_textures(assets, cfg):
    limit = int(cfg["max_texture_size"] or 0)
    findings, count = [], 0
    for package, data in sorted(assets.items()):
        if _class(data) != "Texture2D":
            continue
        dims = _tag(data, "Dimensions")
        if not dims or "x" not in dims:
            continue
        count += 1
        width, height = (int(v) for v in dims.lower().split("x")[:2])
        virtual = _tag(data, "VirtualTextureStreaming") == "True"
        max_size = int(_tag(data, "MaxTextureSize") or 0)
        effective = max(width, height) if not max_size else min(max(width, height), max_size)
        if limit and effective > limit and not virtual:
            findings.append(_finding("warning", "textures", package, "%s, over %d (set Maximum Texture Size, or "
                                     "resize the source)" % (dims, limit)))
        group = _tag(data, "LODGroup") or ""
        if not (_power_of_two(width) and _power_of_two(height)) and group != "TEXTUREGROUP_UI" \
                and (_tag(data, "PowerOfTwoMode") or "None") == "None":
            findings.append(_finding("warning", "textures", package, "%s isn't a power of two, so it has no mips "
                                     "and doesn't stream (fine for UI: set its texture group to UI)" % dims))
    return findings, count


def check_naming(assets, cfg):
    rules = cfg["naming"]
    if rules in (True, None):  # None: asked for by name (checks=['naming'] or 'all') with no rules of its own
        rules = NAMING
    elif not rules:
        return [], 0
    findings, count = [], 0
    for package, data in sorted(assets.items()):
        if _external(package):
            continue
        prefixes = rules.get(_class(data))
        if not prefixes:
            continue
        prefixes = (prefixes,) if isinstance(prefixes, str) else tuple(prefixes)
        count += 1
        name = str(data.asset_name)
        if not name.startswith(prefixes):
            findings.append(_finding("warning", "naming", package, "%s should start with %s" % (
                _class(data), " or ".join(prefixes))))
    return findings, count


def _mentioned_paths():
    """Content paths written in the project's Config/ and Source/ (and its plugins'): config and code that load
    assets by path don't show up as references in the asset registry."""
    root = _project_dir()
    folders = [os.path.join(root, "Config"), os.path.join(root, "Source")]
    plugins = os.path.join(root, "Plugins")
    if os.path.isdir(plugins):
        for name in os.listdir(plugins):
            folders += [os.path.join(plugins, name, "Config"), os.path.join(plugins, name, "Source")]
    found = set()
    for folder in folders:
        for dirpath, _, files in os.walk(folder):
            for file in files:
                if file.lower().endswith((".ini", ".h", ".cpp", ".cs", ".inl", ".py")):
                    try:
                        with open(os.path.join(dirpath, file), encoding="utf-8", errors="replace") as f:
                            found.update(m.split(".")[0].rstrip("/") for m in _PATH_RE.findall(f.read()))
                    except OSError:
                        pass
    return found


def check_unused(assets, cfg):
    registry = _registry()
    options = _dep_options(True, True, management=True)
    mentioned = _mentioned_paths()
    folders = {m for m in mentioned if m.count("/") >= 2}
    findings, count = [], 0
    for package, data in sorted(assets.items()):
        if _external(package) or _class(data) in _NEVER_UNUSED:
            continue
        count += 1
        referencers = [str(r) for r in (registry.get_referencers(package, options) or []) if str(r) != package]
        if referencers or package in mentioned:
            continue
        if any(package.startswith(f + "/") for f in folders):
            continue
        findings.append(_finding("info", "unused", package, "nothing references this %s" % _class(data)))
    return findings, count


def check_blueprints(assets, cfg):
    import bpedit
    _lib()  # the plugin, before compiling anything
    findings, count = [], 0
    for package, data in sorted(assets.items()):
        if _class(data) not in _BLUEPRINTS or _external(package):
            continue
        count += 1
        bp = unreal.load_asset(package)
        if not isinstance(bp, unreal.Blueprint):
            continue
        result = bpedit.compile(bp, raise_on_error=False)
        for message in result["messages"]:
            if message["severity"] in ("error", "warning"):
                findings.append(_finding(message["severity"], "blueprints", package, message["message"]))
        if result["status"] == "error" and not any(m["severity"] == "error" for m in result["messages"]):
            findings.append(_finding("error", "blueprints", package, "failed to compile"))
    return findings, count


def check_data(assets, cfg):
    packages = sorted(p for p, d in assets.items() if not _external(p) and _class(d) != "ObjectRedirector")
    if not packages:
        return [], 0
    result = _call("validate_assets", packages)
    findings = []
    for item in result["assets"]:
        package = item["asset"].split(".")[0]
        for message in item["messages"]:
            if message["severity"] != "info":
                findings.append(_finding(message["severity"], "data", package, message["message"]))
        if item["result"] == "invalid" and not any(m["severity"] == "error" for m in item["messages"]):
            findings.append(_finding("error", "data", package, "invalid (no message given)"))
    return findings, result["checked"]


def _headless():
    """A commandlet run (ue.py --headless): no level editor, and asking for its subsystems crashes."""
    return "-run=" in unreal.SystemLibrary.get_command_line().lower()


def _editor_world():
    if _headless():
        return None
    try:
        sub = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        return sub.get_editor_world() if sub else None
    except Exception:
        return None


def _playing():
    if _headless():
        return False
    try:
        sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        return bool(sub and sub.is_in_play_in_editor())
    except Exception:
        return False


def _dirty_maps():
    return [p.get_name() for p in unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages()]


def check_maps(assets, cfg):
    _lib()
    maps = cfg["maps"]
    if maps == "all":
        maps = sorted(p for p, d in assets.items() if _class(d) == "World")
    elif isinstance(maps, str):
        maps = [maps]
    findings = []
    if not maps:
        world = _editor_world()
        if world is None or world.get_outermost().get_name().startswith("/Temp/"):
            return None, "no map given and no level open (set `maps`, or 'all')"
        maps = [None]
    current = _editor_world()
    original = current.get_outermost().get_name() if current else None
    switching = any(m and m != original for m in maps)
    if switching and current is not None and _dirty_maps():
        raise ValidateError("the open level has unsaved changes; save it before checking other maps")
    try:
        for path in maps:
            if path is None or path == original:
                world = current
            else:
                world = unreal.EditorLoadingAndSavingUtils.load_map(path)
                if world is None:
                    findings.append(_finding("error", "maps", path, "could not load the map"))
                    continue
            result = _call("map_check", world)
            for message in result["messages"]:
                findings.append(_finding(message["severity"], "maps", result["map"], message["message"]))
    finally:
        if switching and original and not original.startswith("/Temp/"):
            unreal.EditorLoadingAndSavingUtils.load_map(original)
    return findings, len(maps)


# --------------------------------------------------------------------------- run and report

def _ignored(finding, rules):
    for rule in rules:
        if all(fnmatch.fnmatchcase(finding.get(k, ""), str(v)) for k, v in rule.items()):
            return True
    return False


def run(roots=None, checks=None, skip=(), maps=None, exclude=None, max_texture_size=None, naming=None,
        fail_on=None, ignore=None):
    """Run the checks (see the module docstring) and return the findings. Settings: .uetk/validate.json, then
    these arguments."""
    cfg = settings(roots=roots, exclude=exclude, checks=checks, maps=maps, max_texture_size=max_texture_size,
                   naming=naming, fail_on=fail_on, ignore=ignore)
    chosen = _checks(cfg["checks"], skip, naming=bool(cfg["naming"]))
    if _playing():
        raise ValidateError("stop PIE first: validation loads and compiles assets")
    _wait_for_registry()
    _refresh(cfg)
    assets = _assets(cfg)
    findings, checked, skipped, seconds = [], {}, {}, {}
    for name in chosen:
        started = time.time()
        try:
            found, count = globals()["check_" + name](assets, cfg)
        except ValidateError as e:
            found, count = None, str(e)
        seconds[name] = round(time.time() - started, 2)
        if found is None:
            skipped[name] = count
            continue
        checked[name] = count
        findings += [f for f in found if not _ignored(f, cfg["ignore"])]
    unique = {}
    for f in findings:  # map check repeats a message once per component
        key = (f["severity"], f["check"], f["asset"], f["message"])
        if key in unique:
            unique[key]["count"] = unique[key].get("count", 1) + 1
        else:
            unique[key] = f
    findings = list(unique.values())
    order = {s: i for i, s in enumerate(SEVERITIES)}
    findings.sort(key=lambda f: (order[f["severity"]], CHECKS.index(f["check"]), f["asset"]))
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES}
    fail_levels = ("error", "warning") if cfg["fail_on"] == "warning" else ("error",)
    return {"roots": cfg["roots"], "findings": findings, "counts": counts,
            "failed": any(counts[s] for s in fail_levels), "checked": checked, "skipped": skipped,
            "seconds": seconds}


def report(result, limit=None):
    """run()'s result as text: one line per finding, then a summary."""
    lines = []
    findings = result["findings"]
    for f in findings[:limit] if limit else findings:
        message = f["message"].replace("\n", " | ") + (" (x%d)" % f["count"] if f.get("count") else "")
        lines.append("%-7s %-11s %s: %s" % (f["severity"], f["check"], f["asset"], message))
    if limit and len(findings) > limit:
        lines.append("... and %d more (--json has them all)" % (len(findings) - limit))
    c = result["counts"]
    checked = ", ".join("%s %d" % (k, v) for k, v in result["checked"].items())
    lines.append("%s: %d error(s), %d warning(s), %d note(s) in %s (checked: %s)" % (
        "FAILED" if result["failed"] else "ok", c["error"], c["warning"], c["info"], ", ".join(result["roots"]),
        checked or "nothing"))
    for name, why in result["skipped"].items():
        lines.append("skipped %s: %s" % (name, why))
    return "\n".join(lines)


def fix_redirectors(roots="/Game"):
    """Fix up every redirector under the roots: referencers resaved to point at the targets, redirectors deleted.
    Returns {'fixed': [...], 'remaining': [...]}."""
    cfg = settings(roots=roots if isinstance(roots, (list, tuple)) else [roots])
    packages = sorted(p for p, d in _assets(cfg).items() if _class(d) == "ObjectRedirector")
    if not packages:
        return {"fixed": [], "remaining": []}
    return _call("fix_redirectors", packages)
