"""Create a UE C++ project from an engine template, the way GameProjectUtils does, without the editor UI.

Follows the template's TemplateDefs.ini rules: copy (minus ignored folders/files), folder renames,
filename and in-file replacements for cpp/h/ini/cs, shared content packs from TemplateResources,
ActiveGameNameRedirects + ActiveClassRedirects, a new ProjectID, and a .uproject.

usage: python new_from_template.py <engine_root> <template> <dest_dir> <ProjectName> <engine_assoc>
"""
import json
import re
import shutil
import sys
import uuid
from pathlib import Path

engine, template, dest, name, assoc = sys.argv[1:6]
engine = Path(engine)
src = engine / "Templates" / template
dest = Path(dest)
assert src.is_dir(), src
assert not (dest / f"{name}.uproject").exists(), "project already exists"

defs = (src / "Config" / "TemplateDefs.ini").read_text(encoding="utf-8-sig")
ignore_dirs = set(re.findall(r"^FoldersToIgnore=(.+)$", defs, re.M))
ignore_files = {f.strip('"').replace("%TEMPLATENAME%", template)
                for f in re.findall(r"^FilesToIgnore=(.+)$", defs, re.M)}
packs = re.findall(r'^SharedContentPacks=\(MountName="(\w+)",DetailLevels=\("(\w+)"', defs, re.M)
TEXT_EXT = {".cpp", ".h", ".ini", ".cs"}


def rename(s: str) -> str:
    s = s.replace(template.upper(), name.upper()).replace(template.lower(), name.lower())
    return re.sub(re.escape(template), name, s, flags=re.I)


class_renames = {}
for f in src.rglob("*"):
    rel = f.relative_to(src)
    if f.is_dir() or rel.parts[0] in ignore_dirs or rel.as_posix() in ignore_files:
        continue
    parts = list(rel.parts)
    if parts[0] == "Source" and len(parts) > 1:
        parts[1] = rename(parts[1]) if parts[1].lower().startswith(template.lower()) else parts[1]
    out_name = rename(f.name) if f.suffix in TEXT_EXT else f.name
    out = dest.joinpath(*parts[:-1], out_name)
    out.parent.mkdir(parents=True, exist_ok=True)
    if f.suffix in TEXT_EXT:
        text = f.read_text(encoding="utf-8-sig")
        out.write_text(rename(text), encoding="utf-8")
        if out_name != f.name and f.suffix == ".h" and "UCLASS" in text:
            class_renames[f.stem] = out.stem
    else:
        shutil.copy2(f, out)

# Shared content packs: TemplateResources/<Detail>/<Mount>/Content -> Content/<Mount>
for mount, detail in packs:
    pack = engine / "Templates" / "TemplateResources" / detail / mount / "Content"
    shutil.copytree(pack, dest / "Content" / mount, dirs_exist_ok=True)

# Redirects so the template's Blueprints find the renamed module and classes.
eng = dest / "Config" / "DefaultEngine.ini"
lines = ["", "[/Script/Engine.Engine]",
         f'+ActiveGameNameRedirects=(OldGameName="/Script/{template}",NewGameName="/Script/{name}")',
         f'+ActiveGameNameRedirects=(OldGameName="{template}",NewGameName="/Script/{name}")']
lines += [f'+ActiveClassRedirects=(OldClassName="{o}",NewClassName="{n}")' for o, n in sorted(class_renames.items())]
eng.write_text(eng.read_text(encoding="utf-8") + "\n".join(lines) + "\n", encoding="utf-8")

game = dest / "Config" / "DefaultGame.ini"
g = uuid.uuid4().hex.upper()
game.write_text(re.sub(r"^ProjectID=.*$", f"ProjectID={g}", game.read_text(encoding="utf-8"), flags=re.M)
                .replace("ProjectName=First Person Template", f"ProjectName={name}"), encoding="utf-8")

up = json.loads((src / f"{template}.uproject").read_text(encoding="utf-8-sig"))
up["EngineAssociation"] = assoc
for m in up.get("Modules", []):
    m["Name"] = rename(m["Name"])
(dest / f"{name}.uproject").write_text(json.dumps(up, indent="\t") + "\n", encoding="utf-8")

print("class renames:", class_renames)
print("packs:", packs)
