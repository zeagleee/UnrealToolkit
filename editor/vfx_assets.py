"""Shared particle textures and materials for vfx.py, generated on demand (no imported art needed).

Textures are written as PNGs from procedural noise and imported; materials are built with MaterialEditingLibrary.
Everything lands in a shared folder (default /Game/VFX/Shared) and is reused once it exists.

  vfx_assets.material('glow')     soft additive dot (sparks, embers, magic, flashes)
  vfx_assets.material('smoke')    translucent smoke puff from a 4x4 flipbook (random frame per particle)
  vfx_assets.material('ribbon')   additive strip that fades across its width (trails)
  vfx_assets.material('mesh')     lit opaque, base colour = particle colour (debris)
  vfx_assets.material('shockwave') additive ring (impacts, ground shockwaves)
"""
import math
import os
import random
import struct
import tempfile
import zlib

import unreal

DEFAULT_FOLDER = "/Game/VFX/Shared"
MEL = unreal.MaterialEditingLibrary
EAL = unreal.EditorAssetLibrary


# --------------------------------------------------------------------------- PNG + noise

def _write_png(path, width, height, rows):
    """rows: list of bytes, each width*3 RGB bytes."""
    raw = b"".join(b"\x00" + row for row in rows)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw, 9)))
        f.write(chunk(b"IEND", b""))


class _ValueNoise:
    """Tileable value noise on a period-sized lattice, smooth-stepped."""

    def __init__(self, period, seed):
        rng = random.Random(seed)
        self.period = period
        self.grid = [[rng.random() for _ in range(period)] for _ in range(period)]

    def __call__(self, x, y):
        p = self.period
        x0, y0 = int(math.floor(x)), int(math.floor(y))
        fx, fy = x - x0, y - y0
        fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
        g = self.grid
        a = g[y0 % p][x0 % p]
        b = g[y0 % p][(x0 + 1) % p]
        c = g[(y0 + 1) % p][x0 % p]
        d = g[(y0 + 1) % p][(x0 + 1) % p]
        return (a + (b - a) * fx) + ((c + (d - c) * fx) - (a + (b - a) * fx)) * fy


def _fbm(noises, x, y):
    total, amp, norm = 0.0, 1.0, 0.0
    for i, n in enumerate(noises):
        total += n(x * (2 ** i), y * (2 ** i)) * amp
        norm += amp
        amp *= 0.5
    return total / norm


def _soft_dot_rows(size):
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            dx, dy = (x + 0.5) / size * 2 - 1, (y + 0.5) / size * 2 - 1
            r = min(1.0, math.sqrt(dx * dx + dy * dy))
            v = (1 - r) ** 2.2  # bright core, long soft falloff
            b = int(round(255 * v))
            row += bytes((b, b, b))
        rows.append(bytes(row))
    return rows


def _smoke_flipbook_rows(cell, grid=4, seed=7):
    """grid x grid smoke puffs: lumpy fbm density inside a noisy round falloff."""
    size = cell * grid
    pixels = [[0] * size for _ in range(size)]
    for gy in range(grid):
        for gx in range(grid):
            idx = gy * grid + gx
            noises = [_ValueNoise(8, seed * 100 + idx * 7 + o) for o in range(4)]
            warp = _ValueNoise(4, seed * 31 + idx)
            for y in range(cell):
                for x in range(cell):
                    u, v = (x + 0.5) / cell, (y + 0.5) / cell
                    dx, dy = u * 2 - 1, v * 2 - 1
                    angle = math.atan2(dy, dx)
                    edge = 0.72 + 0.22 * (warp(math.cos(angle) * 1.5 + 2, math.sin(angle) * 1.5 + 2) - 0.5) * 2
                    r = math.sqrt(dx * dx + dy * dy) / max(edge, 0.2)
                    falloff = max(0.0, 1 - r) ** 1.4
                    density = _fbm(noises, u * 3, v * 3)
                    density = max(0.0, min(1.0, (density - 0.25) * 1.9))
                    value = falloff * (0.35 + 0.65 * density)
                    pixels[gy * cell + y][gx * cell + x] = int(round(255 * min(1.0, value * 1.25)))
    rows = []
    for y in range(size):
        row = bytearray()
        for value in pixels[y]:
            row += bytes((value, value, value))
        rows.append(bytes(row))
    return rows


def _ring_rows(size):
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            dx, dy = (x + 0.5) / size * 2 - 1, (y + 0.5) / size * 2 - 1
            r = math.sqrt(dx * dx + dy * dy)
            v = max(0.0, 1 - abs(r - 0.78) / 0.2) ** 2 if r < 1 else 0.0
            b = int(round(255 * v))
            row += bytes((b, b, b))
        rows.append(bytes(row))
    return rows


TEXTURES = {
    "T_VFX_SoftDot": (128, 128, lambda: _soft_dot_rows(128)),
    "T_VFX_Smoke4x4": (512, 512, lambda: _smoke_flipbook_rows(128)),
    "T_VFX_Ring": (256, 256, lambda: _ring_rows(256)),
}


def texture(name, folder=DEFAULT_FOLDER):
    """Path of a shared mask texture (T_VFX_SoftDot, T_VFX_Smoke4x4, T_VFX_Ring), generating it if missing."""
    path = "%s/%s" % (folder, name)
    if EAL.does_asset_exist(path):
        return path
    width, height, make = TEXTURES[name]
    tmp = os.path.join(tempfile.gettempdir(), "uetk_vfx")
    os.makedirs(tmp, exist_ok=True)
    png = os.path.join(tmp, name + ".png")
    _write_png(png, width, height, make())
    # Not AssetImportTask: in a headless editor the import pipeline reaches for the Content Browser and crashes.
    raw = unreal.UETKVfxLibrary.create_texture_from_file(path, png, False)
    if '"ok":true' not in raw.replace(" ", ""):
        raise RuntimeError("texture creation failed: %s" % raw)
    EAL.save_asset(path, only_if_is_dirty=False)
    return path


# --------------------------------------------------------------------------- materials

def _expr(mat, cls, x, y):
    return MEL.create_material_expression(mat, cls, x, y)


def _new_material(path, blend, shading):
    folder, name = path.rsplit("/", 1)
    mat = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, unreal.Material, unreal.MaterialFactoryNew())
    if mat is None:
        raise RuntimeError("could not create %s" % path)
    mat.set_editor_property("blend_mode", blend)
    mat.set_editor_property("shading_model", shading)
    for usage in ("used_with_niagara_sprites", "used_with_niagara_ribbons", "used_with_niagara_mesh_particles"):
        mat.set_editor_property(usage, True)
    return mat


def _mask_sample(mat, tex_path, x, y, sub_uv=False):
    cls = unreal.MaterialExpressionParticleSubUV if sub_uv else unreal.MaterialExpressionTextureSample
    sample = _expr(mat, cls, x, y)
    sample.set_editor_property("texture", EAL.load_asset(tex_path))
    sample.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR)
    if sub_uv:
        sample.set_editor_property("blend", True)
    return sample


def _finish(mat, path):
    MEL.layout_material_expressions(mat)
    MEL.recompile_material(mat)
    EAL.save_asset(path, only_if_is_dirty=False)
    return path


def _build_glow(path, folder):
    mat = _new_material(path, unreal.BlendMode.BLEND_ADDITIVE, unreal.MaterialShadingModel.MSM_UNLIT)
    mask = _mask_sample(mat, texture("T_VFX_SoftDot", folder), -700, 0)
    color = _expr(mat, unreal.MaterialExpressionParticleColor, -700, 250)
    rgb_mask = _expr(mat, unreal.MaterialExpressionMultiply, -400, 100)
    MEL.connect_material_expressions(color, "RGB", rgb_mask, "A")
    MEL.connect_material_expressions(mask, "R", rgb_mask, "B")
    out = _expr(mat, unreal.MaterialExpressionMultiply, -200, 100)
    MEL.connect_material_expressions(rgb_mask, "", out, "A")
    MEL.connect_material_expressions(color, "A", out, "B")
    MEL.connect_material_property(out, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    return _finish(mat, path)


def _build_ring(path, folder):
    mat = _new_material(path, unreal.BlendMode.BLEND_ADDITIVE, unreal.MaterialShadingModel.MSM_UNLIT)
    mat.set_editor_property("two_sided", True)
    mask = _mask_sample(mat, texture("T_VFX_Ring", folder), -700, 0)
    color = _expr(mat, unreal.MaterialExpressionParticleColor, -700, 250)
    rgb_mask = _expr(mat, unreal.MaterialExpressionMultiply, -400, 100)
    MEL.connect_material_expressions(color, "RGB", rgb_mask, "A")
    MEL.connect_material_expressions(mask, "R", rgb_mask, "B")
    out = _expr(mat, unreal.MaterialExpressionMultiply, -200, 100)
    MEL.connect_material_expressions(rgb_mask, "", out, "A")
    MEL.connect_material_expressions(color, "A", out, "B")
    MEL.connect_material_property(out, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    return _finish(mat, path)


def _build_smoke(path, folder):
    mat = _new_material(path, unreal.BlendMode.BLEND_TRANSLUCENT, unreal.MaterialShadingModel.MSM_UNLIT)
    sample = _mask_sample(mat, texture("T_VFX_Smoke4x4", folder), -900, 0, sub_uv=True)
    color = _expr(mat, unreal.MaterialExpressionParticleColor, -900, 300)
    # Colour: particle colour, a little darker where the puff is thin.
    shade = _expr(mat, unreal.MaterialExpressionLinearInterpolate, -550, -100)
    lo = _expr(mat, unreal.MaterialExpressionConstant, -750, -200)
    lo.set_editor_property("r", 0.55)
    hi = _expr(mat, unreal.MaterialExpressionConstant, -750, -150)
    hi.set_editor_property("r", 1.0)
    MEL.connect_material_expressions(lo, "", shade, "A")
    MEL.connect_material_expressions(hi, "", shade, "B")
    MEL.connect_material_expressions(sample, "R", shade, "Alpha")
    emissive = _expr(mat, unreal.MaterialExpressionMultiply, -300, -50)
    MEL.connect_material_expressions(color, "RGB", emissive, "A")
    MEL.connect_material_expressions(shade, "", emissive, "B")
    MEL.connect_material_property(emissive, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    # Opacity: puff density x particle alpha x depth fade (soft where it meets geometry).
    density = _expr(mat, unreal.MaterialExpressionMultiply, -550, 200)
    MEL.connect_material_expressions(sample, "R", density, "A")
    MEL.connect_material_expressions(color, "A", density, "B")
    fade = _expr(mat, unreal.MaterialExpressionDepthFade, -300, 250)
    fade.set_editor_property("fade_distance_default", 40.0)
    MEL.connect_material_expressions(density, "", fade, "Opacity")
    MEL.connect_material_property(fade, "", unreal.MaterialProperty.MP_OPACITY)
    return _finish(mat, path)


def _build_ribbon(path, folder):
    mat = _new_material(path, unreal.BlendMode.BLEND_ADDITIVE, unreal.MaterialShadingModel.MSM_UNLIT)
    mat.set_editor_property("two_sided", True)
    uv = _expr(mat, unreal.MaterialExpressionTextureCoordinate, -1100, 0)
    v = _expr(mat, unreal.MaterialExpressionComponentMask, -900, 0)
    v.set_editor_property("r", False)
    v.set_editor_property("g", True)
    MEL.connect_material_expressions(uv, "", v, "")
    # 1 - |2v - 1|: bright centre line fading to the ribbon's edges.
    two = _expr(mat, unreal.MaterialExpressionConstant, -900, 120)
    two.set_editor_property("r", 2.0)
    scaled = _expr(mat, unreal.MaterialExpressionMultiply, -750, 50)
    MEL.connect_material_expressions(v, "", scaled, "A")
    MEL.connect_material_expressions(two, "", scaled, "B")
    one = _expr(mat, unreal.MaterialExpressionConstant, -750, 170)
    one.set_editor_property("r", 1.0)
    centred = _expr(mat, unreal.MaterialExpressionSubtract, -600, 50)
    MEL.connect_material_expressions(scaled, "", centred, "A")
    MEL.connect_material_expressions(one, "", centred, "B")
    absolute = _expr(mat, unreal.MaterialExpressionAbs, -480, 50)
    MEL.connect_material_expressions(centred, "", absolute, "")
    inverted = _expr(mat, unreal.MaterialExpressionOneMinus, -380, 50)
    MEL.connect_material_expressions(absolute, "", inverted, "")
    power = _expr(mat, unreal.MaterialExpressionPower, -260, 50)
    exponent = _expr(mat, unreal.MaterialExpressionConstant, -380, 150)
    exponent.set_editor_property("r", 2.0)
    MEL.connect_material_expressions(inverted, "", power, "Base")
    MEL.connect_material_expressions(exponent, "", power, "Exp")
    color = _expr(mat, unreal.MaterialExpressionParticleColor, -600, 300)
    rgb = _expr(mat, unreal.MaterialExpressionMultiply, -150, 150)
    MEL.connect_material_expressions(color, "RGB", rgb, "A")
    MEL.connect_material_expressions(power, "", rgb, "B")
    out = _expr(mat, unreal.MaterialExpressionMultiply, 0, 150)
    MEL.connect_material_expressions(rgb, "", out, "A")
    MEL.connect_material_expressions(color, "A", out, "B")
    MEL.connect_material_property(out, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    return _finish(mat, path)


def _build_mesh(path, folder):
    mat = _new_material(path, unreal.BlendMode.BLEND_OPAQUE, unreal.MaterialShadingModel.MSM_DEFAULT_LIT)
    color = _expr(mat, unreal.MaterialExpressionParticleColor, -500, 0)
    MEL.connect_material_property(color, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
    rough = _expr(mat, unreal.MaterialExpressionConstant, -500, 200)
    rough.set_editor_property("r", 0.7)
    MEL.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)
    return _finish(mat, path)


def _build_flat(path, folder):
    mat = _new_material(path, unreal.BlendMode.BLEND_MASKED, unreal.MaterialShadingModel.MSM_UNLIT)
    mat.set_editor_property("two_sided", True)
    color = _expr(mat, unreal.MaterialExpressionParticleColor, -500, 0)
    MEL.connect_material_property(color, "RGB", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    MEL.connect_material_property(color, "A", unreal.MaterialProperty.MP_OPACITY_MASK)
    return _finish(mat, path)


def _build_backdrop(path, folder):
    """Unlit plane colour for previews (vfx.preview draws it behind the effect)."""
    mat = _new_material(path, unreal.BlendMode.BLEND_OPAQUE, unreal.MaterialShadingModel.MSM_UNLIT)
    param = _expr(mat, unreal.MaterialExpressionVectorParameter, -500, 0)
    param.set_editor_property("parameter_name", "Color")
    param.set_editor_property("default_value", unreal.LinearColor(0.18, 0.18, 0.18, 1))
    MEL.connect_material_property(param, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    return _finish(mat, path)


MATERIALS = {
    "backdrop": ("M_VFX_Backdrop", _build_backdrop),
    "flat": ("M_VFX_Flat", _build_flat),
    "glow": ("M_VFX_Glow", _build_glow),
    "smoke": ("M_VFX_Smoke", _build_smoke),
    "ribbon": ("M_VFX_Ribbon", _build_ribbon),
    "mesh": ("M_VFX_MeshLit", _build_mesh),
    "shockwave": ("M_VFX_Ring", _build_ring),
}


def material(kind, folder=DEFAULT_FOLDER, rebuild=False):
    """Path of a shared particle material ('glow', 'smoke', 'ribbon', 'mesh', 'shockwave'), building it if missing.
    Any other string is returned unchanged (treated as a material path)."""
    if kind not in MATERIALS:
        return kind
    name, build = MATERIALS[kind]
    path = "%s/%s" % (folder, name)
    if EAL.does_asset_exist(path):
        if not rebuild:
            return path
        EAL.delete_asset(path)
    return build(path, folder)
