"""importer - bring source files into the project: static and skeletal meshes, animations, textures and sounds, with
presets. Runs inside UnrealEditor (or headless); pre-imported as `importer` in `ue.py py`.

    importer.run(['D:/Art/Props', 'D:/Art/SM_Rock.fbx'], '/Game/Props')   # files and folders (folders recursively)
    importer.run('D:/Art/A_Run.fbx', '/Game/Anims', skeleton='/Game/Characters/SK_Hero_Skeleton')
    importer.run('D:/Art/T_Rock_ORM.png', '/Game/Props', texture='masks')  # presets come from the name; this overrides
    importer.reimport('/Game/Props')            # every asset there whose source file changed since its import
    importer.sources('/Game/Props')             # each imported asset's source file, and whether it changed

What each file becomes:
    .fbx            static mesh, skeletal mesh or animation, from its content (polygons, skin clusters, bones, curves)
                    unless the name says (SM_, SK_/SKM_, A_/AS_/Anim_) or kind= does
    .obj            static mesh            .gltf .glb   whatever the file holds (the engine's glTF importer)
    .png .jpg .jpeg .tga .bmp .tif .tiff .psd .exr .hdr .dds   texture
    .wav .ogg .flac .aif .aiff                                 sound
Texture presets (texture=, or from the name's last _suffix): color (default: _D _BaseColor _Albedo...), normal (_N
    _Normal _Nrm), masks (_ORM _ARM _RMA _M _R _AO _Rough _Metal _Mask...), grayscale (_H _Height _Disp _Opacity),
    linear, hdr (.exr .hdr), ui. A dict sets texture properties directly: {'srgb': False, 'lod_group': 'ui'}.
Options (keywords, or rules=[{'match': '*_UI_*.png', 'texture': 'ui'}] per file name):
    kind='static'|'skeletal'|'animation'   materials=True  textures=True (from the mesh file)
    skeleton=PATH (skeletal: share one; animation: required)   physics_asset=True   animations=False (in a mesh file)
    collision=True|False|'box'|'sphere'|'capsule'|'convex'|'complex'|'none'   combine=True   normals='import'|'compute'|
    'import_tangents'   scale=1.0   nanite=None   lightmap_uvs=None   lod_group=None (static mesh LOD group)
    looping=None  sound_class=PATH  attenuation=PATH  volume=None (sounds)
    name=  (one file: the asset name; default the file name)   flat=False (folders: don't mirror subfolders)
Every imported asset records its source file and the options in its package metadata (tag UETK.Import), so
reimport() repeats the same import, and sources() tells which source files changed (by content hash).
"""
import fnmatch
import hashlib
import json
import os
import re
import time

import unreal

import uprop
from uprop import PropError

EAL = unreal.EditorAssetLibrary
TAG = "UETK.Import"

MESH_EXT = {".fbx", ".obj", ".gltf", ".glb"}
TEXTURE_EXT = {".png", ".jpg", ".jpeg", ".tga", ".bmp", ".tif", ".tiff", ".psd", ".exr", ".hdr", ".dds"}
SOUND_EXT = {".wav", ".ogg", ".flac", ".aif", ".aiff"}
MAIN_CLASSES = ("StaticMesh", "SkeletalMesh", "AnimSequence", "Texture2D", "TextureCube", "SoundWave")

TEXTURE_PRESETS = {
    "color": {"srgb": True, "compression_settings": "TC_DEFAULT"},
    "normal": {"srgb": False, "compression_settings": "TC_NORMALMAP", "lod_group": "TEXTUREGROUP_WORLD_NORMAL_MAP"},
    "masks": {"srgb": False, "compression_settings": "TC_MASKS"},
    "grayscale": {"srgb": False, "compression_settings": "TC_GRAYSCALE"},
    "linear": {"srgb": False, "compression_settings": "TC_DEFAULT"},
    "hdr": {"srgb": False, "compression_settings": "TC_HDR"},
    "ui": {"srgb": True, "compression_settings": "TC_EDITOR_ICON", "lod_group": "TEXTUREGROUP_UI",
           "mip_gen_settings": "TMGS_NO_MIPMAPS", "never_stream": True},
}
_SUFFIXES = {
    "normal": {"n", "normal", "normals", "nrm", "nor", "nml"},
    "masks": {"orm", "arm", "rma", "mra", "rmo", "m", "r", "ao", "mask", "masks", "rough", "roughness", "metal",
              "metallic", "metalness", "occlusion", "spec", "specular", "gloss"},
    "grayscale": {"h", "height", "disp", "displacement", "opacity", "alpha", "bump"},
}
_OPTIONS = {"kind", "materials", "textures", "skeleton", "physics_asset", "animations", "collision", "combine",
            "normals", "scale", "nanite", "lightmap_uvs", "lod_group", "texture", "looping", "sound_class",
            "attenuation", "volume", "name", "morph_targets"}


class ImportFailed(PropError):
    pass


# --------------------------------------------------------------------------- classifying files

def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _fbx_kind(path):
    """'static', 'skeletal' or 'animation' from what an FBX file holds, with the reason."""
    with open(path, "rb") as f:
        data = f.read()
    mesh = b"PolygonVertexIndex" in data
    skin = b"Cluster" in data
    bones = b"LimbNode" in data
    curves = b"AnimationCurve" in data
    if mesh and skin:
        return "skeletal", "the FBX has a skinned mesh"
    if mesh:
        return "static", "the FBX has a mesh and no skin"
    if bones and curves:
        return "animation", "the FBX has bones and animation curves but no mesh"
    return None, "the FBX has no mesh and no animated bones"


def _name_kind(stem):
    prefix = stem.split("_", 1)[0].upper() if "_" in stem else ""
    return {"SM": "static", "SK": "skeletal", "SKM": "skeletal", "A": "animation", "AS": "animation",
            "ANIM": "animation"}.get(prefix)


def _texture_preset(stem, ext):
    if ext in (".exr", ".hdr"):
        return "hdr"
    suffix = stem.rsplit("_", 1)[-1].lower() if "_" in stem else ""
    for preset, names in _SUFFIXES.items():
        if suffix in names:
            return preset
    return "color"


def _safe_name(stem):
    name = re.sub(r"[^0-9A-Za-z_]", "_", stem)
    return name if name and not name[0].isdigit() else "_" + name


def classify(path, options=None):
    """What a source file will become: {'type': mesh|texture|sound, 'kind', 'why'} (raises for unknown types)."""
    options = options or {}
    stem, ext = os.path.splitext(os.path.basename(path))
    ext = ext.lower()
    if ext in TEXTURE_EXT:
        preset = options.get("texture") or _texture_preset(stem, ext)
        why = "texture= given" if options.get("texture") else ("the extension" if ext in (".exr", ".hdr") else
                                                               "the name's suffix" if preset != "color" else "default")
        return {"type": "texture", "kind": preset if isinstance(preset, str) else "custom", "why": why}
    if ext in SOUND_EXT:
        return {"type": "sound", "kind": "sound", "why": "the extension"}
    if ext not in MESH_EXT:
        raise ImportFailed("can't import %s files (%s)" % (ext or "extension-less", os.path.basename(path)))
    if options.get("kind"):
        if options["kind"] not in ("static", "skeletal", "animation"):
            raise ImportFailed("kind= is static, skeletal or animation, not %r" % options["kind"])
        return {"type": "mesh", "kind": options["kind"], "why": "kind= given"}
    if ext == ".obj":
        return {"type": "mesh", "kind": "static", "why": "OBJ holds static meshes"}
    if ext in (".gltf", ".glb"):
        return {"type": "mesh", "kind": "gltf", "why": "the glTF importer decides from the file"}
    kind, why = _fbx_kind(path)
    named = _name_kind(stem)
    if named and named != kind and kind is not None:
        why = "the name's prefix (%s; %s)" % (named, why)
        kind = named
    if kind is None:
        raise ImportFailed("%s: %s" % (os.path.basename(path), why))
    return {"type": "mesh", "kind": kind, "why": why}


# --------------------------------------------------------------------------- the import itself

def _fbx_options(kind, o):
    ui = unreal.FbxImportUI()
    materials = o.get("materials", True) and kind != "animation"
    ui.set_editor_property("import_materials", bool(materials))
    ui.set_editor_property("import_textures", bool(materials and o.get("textures", True)))
    if kind == "static":
        ui.set_editor_property("import_mesh", True)
        ui.set_editor_property("import_as_skeletal", False)
        ui.set_editor_property("mesh_type_to_import", unreal.FBXImportType.FBXIT_STATIC_MESH)
        ui.set_editor_property("import_animations", False)
        data = ui.get_editor_property("static_mesh_import_data")
        data.set_editor_property("combine_meshes", bool(o.get("combine", True)))
        data.set_editor_property("auto_generate_collision", o.get("collision", True) is not False)
        if o.get("lightmap_uvs") is not None:
            data.set_editor_property("generate_lightmap_u_vs", bool(o["lightmap_uvs"]))
        if o.get("nanite") is not None:
            data.set_editor_property("build_nanite", bool(o["nanite"]))
    elif kind == "skeletal":
        ui.set_editor_property("import_mesh", True)
        ui.set_editor_property("import_as_skeletal", True)
        ui.set_editor_property("mesh_type_to_import", unreal.FBXImportType.FBXIT_SKELETAL_MESH)
        ui.set_editor_property("import_animations", bool(o.get("animations", False)))
        ui.set_editor_property("create_physics_asset", bool(o.get("physics_asset", True)))
        if o.get("skeleton"):
            ui.set_editor_property("skeleton", _skeleton(o["skeleton"]))
        data = ui.get_editor_property("skeletal_mesh_import_data")
        data.set_editor_property("import_morph_targets", bool(o.get("morph_targets", True)))
    else:  # animation
        ui.set_editor_property("import_mesh", False)
        ui.set_editor_property("import_as_skeletal", True)
        ui.set_editor_property("mesh_type_to_import", unreal.FBXImportType.FBXIT_ANIMATION)
        ui.set_editor_property("import_animations", True)
        ui.set_editor_property("create_physics_asset", False)
        ui.set_editor_property("skeleton", _skeleton(o["skeleton"]))
        data = ui.get_editor_property("anim_sequence_import_data")
    if o.get("normals") and kind != "animation":
        method = {"import": "FBXNIM_IMPORT_NORMALS", "compute": "FBXNIM_COMPUTE_NORMALS",
                  "import_tangents": "FBXNIM_IMPORT_NORMALS_AND_TANGENTS"}.get(o["normals"])
        if method is None:
            raise ImportFailed("normals= is import, compute or import_tangents, not %r" % o["normals"])
        data.set_editor_property("normal_import_method", getattr(unreal.FBXNormalImportMethod, method))
    if o.get("scale") is not None:
        data.set_editor_property("import_uniform_scale", float(o["scale"]))
    return ui


def _skeleton(path):
    obj = uprop.asset(path)
    if isinstance(obj, unreal.SkeletalMesh):
        obj = obj.get_editor_property("skeleton")
    if not isinstance(obj, unreal.Skeleton):
        raise ImportFailed("%s is not a Skeleton (or a skeletal mesh)" % path)
    return obj


def _assets_in(folder):
    reg = unreal.AssetRegistryHelpers.get_asset_registry()
    return {str(d.package_name) for d in reg.get_assets_by_path(folder, recursive=True)}


def _main_asset(paths, name):
    objs = [unreal.load_object(None, p) for p in paths]
    objs = [o for o in objs if o is not None]
    ranked = sorted(objs, key=lambda o: (o.get_class().get_name() not in MAIN_CLASSES, o.get_name() != name))
    return ranked[0] if ranked else None


def _apply_texture(tex, preset):
    props = dict(TEXTURE_PRESETS[preset]) if isinstance(preset, str) else dict(preset)
    if isinstance(preset, str) and preset not in TEXTURE_PRESETS:
        raise ImportFailed("texture preset %r is not one of %s" % (preset, ", ".join(TEXTURE_PRESETS)))
    for key, value in props.items():
        uprop.set_prop(tex, key, value)
    return props


def _apply_static(mesh, o):
    """Collision shape and LOD group after import: the plugin's functions work headless, where the editor's
    StaticMeshEditorSubsystem doesn't exist."""
    done = {}
    lib = getattr(unreal, "UETKEditorLibrary", None)
    shape = o.get("collision")
    if isinstance(shape, str):
        if lib is None or not hasattr(lib, "set_simple_collision"):
            raise ImportFailed("collision=%r needs the UETKEditor plugin (ue.py plugins install)" % shape)
        result = json.loads(lib.set_simple_collision(mesh, shape))
        if not result.pop("ok", False):
            raise ImportFailed(result.get("error"))
        done["collision"] = shape
    if o.get("lod_group"):
        if lib is None or not hasattr(lib, "set_lod_group"):
            raise ImportFailed("lod_group= needs the UETKEditor plugin (ue.py plugins install)")
        result = json.loads(lib.set_lod_group(mesh, unreal.Name(o["lod_group"])))
        if not result.pop("ok", False):
            raise ImportFailed(result.get("error"))
        done["lod_group"] = o["lod_group"]
    return done


def _apply_skeletal(mesh, o):
    """A physics asset when asked for one and the import didn't make it (the FBX options don't, through Interchange)."""
    if not o.get("physics_asset", True) or mesh.get_editor_property("physics_asset") is not None:
        return {}
    try:
        made = unreal.SkeletalMeshEditorSubsystem.create_physics_asset(mesh, True, 0)
    except TypeError:  # 5.6: create_physics_asset(mesh)
        made = unreal.SkeletalMeshEditorSubsystem.create_physics_asset(mesh)
    if made is None:
        return {"physics_asset": "could not be created"}
    wanted = mesh.get_path_name().split(".")[0] + "_PhysicsAsset"
    current = made.get_path_name().split(".")[0]
    if current != wanted and not uprop.exists(wanted):
        EAL.save_loaded_asset(made, False)
        EAL.rename_asset(current, wanted)
        current = wanted
    EAL.save_loaded_asset(uprop.asset(current), False)
    return {"physics_asset": current}


def _apply_sound(wave, o):
    done = {}
    for key, prop in (("looping", "looping"), ("volume", "volume"), ("sound_class", "sound_class_object"),
                      ("attenuation", "attenuation_settings")):
        if o.get(key) is not None:
            uprop.set_prop(wave, prop, o[key])
            done[key] = o[key]
    return done


def _import_one(path, dest, o, save, skeletons=()):
    info = classify(path, o)
    stem = os.path.splitext(os.path.basename(path))[0]
    name = _safe_name(o.get("name") or stem)
    record = {"source": path.replace("\\", "/"), "type": info["type"], "kind": info["kind"], "why": info["why"]}
    if info["kind"] == "animation" and not o.get("skeleton"):
        # Caught here: a failed import that reaches the engine crashes a headless 5.7 run.
        if len(set(skeletons)) != 1:
            raise ImportFailed("%s is an animation: give skeleton= (a Skeleton or skeletal mesh path)%s" % (
                os.path.basename(path), "" if not skeletons else "; this run imported several: " + ", ".join(sorted(set(skeletons)))))
        o = dict(o, skeleton=skeletons[0])
        record["skeleton"] = skeletons[0] + " (the skeletal mesh imported in this run)"
    before = _assets_in(dest)
    task = unreal.AssetImportTask()
    for key, value in (("filename", path), ("destination_path", dest), ("destination_name", name),
                       ("automated", True), ("replace_existing", True), ("save", False)):
        task.set_editor_property(key, value)
    if info["type"] == "mesh" and info["kind"] in ("static", "skeletal", "animation"):
        task.set_editor_property("options", _fbx_options(info["kind"], o))
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    paths = [str(p) for p in task.get_editor_property("imported_object_paths")]
    if not paths:
        raise ImportFailed("the engine imported nothing from %s (its messages are in the log)" % os.path.basename(path))
    main = _main_asset(paths, name)
    applied = {}
    if isinstance(main, unreal.Texture):
        applied = _apply_texture(main, o.get("texture") or info["kind"])
    elif isinstance(main, unreal.StaticMesh):
        applied = _apply_static(main, o)
    elif isinstance(main, unreal.SkeletalMesh):
        applied = _apply_skeletal(main, o)
    elif isinstance(main, unreal.SoundWave):
        applied = _apply_sound(main, o)
    assets = []
    for p in paths:
        package = p.split(".")[0]
        obj = unreal.load_object(None, p)
        assets.append({"path": package, "class": obj.get_class().get_name() if obj else None,
                       "status": "replaced" if package in before else "new"})
    # Remember where it came from and how, in the asset itself: reimport() and sources() read this.
    kept = {k: v for k, v in o.items() if k in _OPTIONS and k != "name" and v is not None}
    if info["kind"] == "skeletal" and isinstance(main, unreal.SkeletalMesh) and not kept.get("skeleton"):
        kept["skeleton"] = uprop.short_path(main.get_editor_property("skeleton").get_path_name())
    tag = {"source": record["source"], "md5": _md5(path), "size": os.path.getsize(path), "kind": info["kind"],
           "dest": dest, "name": name, "options": kept, "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    if main is not None:
        EAL.set_metadata_tag(main, TAG, json.dumps(tag))
    if save:
        packages = {}
        for p in paths:
            obj = unreal.load_object(None, p)
            if obj is not None:
                packages[p.split(".")[0]] = obj.get_outermost()
        unreal.EditorLoadingAndSavingUtils.save_packages(list(packages.values()), False)
    record.update(main=main.get_path_name().split(".")[0] if main else None, assets=assets)
    if applied:
        record["applied"] = applied
    return record


_SYNC_CVAR = "Interchange.FeatureFlags.Import.SyncToBrowser"


class _NoBrowserSync:
    """In a headless run, turn off Interchange's Content Browser sync while importing. In 5.7 that console variable
    overrides AssetImportTask's "don't sync", and syncing without a Content Browser asserts
    (CurrentApplication.IsValid()) and kills the run. 5.8 skips it in commandlets itself."""

    def __enter__(self):
        self.old = None
        if "-run=" in unreal.SystemLibrary.get_command_line().lower():
            try:
                self.old = unreal.SystemLibrary.get_console_variable_int_value(_SYNC_CVAR)
                unreal.SystemLibrary.execute_console_command(None, "%s 0" % _SYNC_CVAR)
            except Exception:
                self.old = None
        return self

    def __exit__(self, *exc):
        if self.old is not None:
            unreal.SystemLibrary.execute_console_command(None, "%s %d" % (_SYNC_CVAR, self.old))
        return False


def _files(paths, flat):
    """(file, subfolder relative to the folder it was found in) for files and folders."""
    out = []
    for p in ([paths] if isinstance(paths, str) else paths):
        p = os.path.abspath(p)
        if os.path.isdir(p):
            for root, _dirs, names in os.walk(p):
                rel = os.path.relpath(root, p).replace("\\", "/")
                for n in sorted(names):
                    ext = os.path.splitext(n)[1].lower()
                    if ext in MESH_EXT | TEXTURE_EXT | SOUND_EXT:
                        out.append((os.path.join(root, n), "" if flat or rel == "." else rel))
        elif os.path.isfile(p):
            out.append((p, ""))
        else:
            raise ImportFailed("no such file or folder: %s" % p)
    return out


def run(paths, dest, rules=None, save=True, flat=False, **options):
    """Import files and folders (recursively) into dest; subfolders are mirrored unless flat=True. Options and
    presets are in the module doc; rules=[{'match': glob on the file name, ...options}] override them per file.
    Returns one record per file: source, kind and why, the assets made ('new' or 'replaced'), presets applied,
    or 'error'. A file that fails doesn't stop the others."""
    unknown = set(options) - _OPTIONS
    if unknown:
        raise ImportFailed("unknown option(s) %s (options: %s)" % (", ".join(sorted(unknown)), ", ".join(sorted(_OPTIONS))))
    dest = dest.rstrip("/")
    if not dest.startswith("/"):
        raise ImportFailed("dest is a content folder like /Game/Props")
    files = _files(paths, flat)
    if options.get("name") and len(files) != 1:
        raise ImportFailed("name= is for importing one file")

    def order(item):  # skeletal meshes before the animations that need their skeletons
        try:
            kind = classify(item[0], options)["kind"]
        except PropError:
            kind = None
        return ({"skeletal": 0, "animation": 2}.get(kind, 1), item[0].lower())
    records, skeletons = [], []
    with _NoBrowserSync():
        _run_files(sorted(files, key=order), dest, rules, options, save, records, skeletons)
    return records


def _run_files(files, dest, rules, options, save, records, skeletons):
    for path, sub in files:
        o = dict(options)
        for rule in rules or []:
            if fnmatch.fnmatch(os.path.basename(path).lower(), str(rule.get("match", "")).lower()):
                o.update({k: v for k, v in rule.items() if k != "match"})
        folder = dest + ("/" + sub if sub else "")
        try:
            record = _import_one(path, folder, o, save, skeletons)
            records.append(record)
            if record["kind"] == "skeletal" and record.get("main"):
                mesh = uprop.asset(record["main"])
                skeletons.append(uprop.short_path(mesh.get_editor_property("skeleton").get_path_name()))
        except PropError as exc:
            records.append({"source": path.replace("\\", "/"), "error": str(exc)})


# --------------------------------------------------------------------------- reimport and status

def _tagged(target):
    """{asset package: tag dict} for an asset or every asset under a folder that the toolkit imported."""
    if EAL.does_asset_exist(target.split(".")[0]) and not EAL.does_directory_exist(target):
        packages = [target.split(".")[0]]
    else:
        packages = sorted(_assets_in(target.rstrip("/")))
    out = {}
    for package in packages:
        obj = uprop.asset(package)
        raw = EAL.get_metadata_tag(obj, TAG)
        if raw:
            try:
                out[package] = json.loads(raw)
            except ValueError:
                pass
    return out


def sources(target="/Game"):
    """Each toolkit-imported asset under target (or one asset): its source file, and whether that file is missing,
    changed (different content) or unchanged since the import."""
    out = {}
    for package, tag in _tagged(target).items():
        src = tag["source"]
        if not os.path.isfile(src):
            state = "missing"
        else:
            state = "unchanged" if _md5(src) == tag.get("md5") else "changed"
        out[package] = {"source": src, "state": state, "kind": tag.get("kind"), "imported": tag.get("time")}
    return out


def reimport(target="/Game", all=False, save=True):
    """Import again, with the options recorded at import, every toolkit-imported asset under target (or one asset)
    whose source file changed; all=True reimports unchanged ones too. Returns the import records."""
    records = []
    with _NoBrowserSync():
        _reimport(target, all, save, records)
    return records


def _reimport(target, all, save, records):
    for package, info in sources(target).items():
        if info["state"] == "missing" or (info["state"] == "unchanged" and not all):
            continue
        tag = _tagged(package)[package]
        folder, name = package.rsplit("/", 1)
        options = dict(tag.get("options") or {})
        options["name"] = tag.get("name") or name
        folder = tag.get("dest") or folder  # a glTF's assets sit in subfolders of where it was imported
        try:
            records.append(_import_one(tag["source"], folder, options, save))
        except PropError as exc:
            records.append({"source": tag["source"], "error": str(exc)})


def report(result):
    """run()/reimport() records or sources() as readable lines."""
    lines = []
    if isinstance(result, dict):  # sources()
        for package, info in sorted(result.items()):
            lines.append("%-9s %-40s <- %s" % (info["state"], package, info["source"]))
        counts = {}
        for info in result.values():
            counts[info["state"]] = counts.get(info["state"], 0) + 1
        lines.append("%d imported assets: %s" % (len(result), ", ".join("%d %s" % (n, k) for k, n in sorted(counts.items()))))
        return "\n".join(lines)
    made = errors = 0
    for r in result:
        name = r["source"].rsplit("/", 1)[-1]
        if "error" in r:
            errors += 1
            lines.append("ERROR  %s: %s" % (name, r["error"]))
            continue
        assets = ", ".join("%s%s" % (a["path"], "" if a["status"] == "new" else " (replaced)") for a in r["assets"])
        made += len(r["assets"])
        applied = r.get("applied")
        lines.append("%-24s %-9s %s" % (name, r["kind"], assets))
        detail = [r["why"]] + (["skeleton " + r["skeleton"]] if r.get("skeleton") else []) + \
            ["%s=%s" % kv for kv in (applied or {}).items()]
        lines.append("%-24s           %s" % ("", "; ".join(detail)))
    lines.append("%d file(s), %d asset(s), %d error(s)" % (len(result), made, errors))
    return "\n".join(lines)
