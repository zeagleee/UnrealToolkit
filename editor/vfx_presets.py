"""Built-in VFX presets: each returns a spec for vfx.build() (format in vfx.py's docstring).

Pure Python (no `unreal` import), so `ue.py vfx presets` can list them without an editor.
Common parameters: color (RGB, 0-1), scale (multiplies sizes, distances and speeds), intensity (glow brightness).
Start from a preset and edit the returned dict for anything they don't cover:
    spec = vfx_presets.make('fire', scale=2); spec['emitters'][0]['spawn']['rate'] = 80; vfx.build(spec, path)
Each spec's 'preview' block holds the times and camera that show it best (vfx.create(..., preview_after=True)).
"""

PRESETS = {}


def preset(description):
    def register(fn):
        PRESETS[fn.__name__] = (fn, description)
        return fn
    return register


def catalog():
    """{name: {'description', 'params'}} with each preset's parameters and defaults."""
    import inspect
    out = {}
    for name, (fn, description) in PRESETS.items():
        params = {k: v.default for k, v in inspect.signature(fn).parameters.items()}
        out[name] = {"description": description, "params": params}
    return out


def make(name, **params):
    if name not in PRESETS:
        raise KeyError("unknown preset %r (presets: %s)" % (name, ", ".join(sorted(PRESETS))))
    return PRESETS[name][0](**params)


# --------------------------------------------------------------------------- helpers

def _rgba(color, k=1.0, alpha=1.0):
    r, g, b = list(color)[:3]
    return [r * k, g * k, b * k, alpha]


def _box_bounds(half_xy, z0, z1):
    return [[-half_xy, -half_xy, z0], [half_xy, half_xy, z1]]


FADE_IN_OUT = [[0, 0], [0.15, 1], [0.7, 1], [1, 0]]


# --------------------------------------------------------------------------- one-shots

@preset("burst of hot sparks that streak, fall and cool (one-shot)")
def sparks(color=(1.0, 0.55, 0.15), scale=1.0, intensity=12.0, count=60, spread=60):
    s = scale
    return {
        "loop": "once", "duration": 2.0, "preview": {"times": [0.05, 0.2, 0.45, 0.8]},
        "emitters": [{
            "name": "Sparks", "spawn": {"burst": count},
            "lifetime": [0.5, 1.2],
            "color": _rgba(color, intensity),
            "size": {"xy_random": [[2.0 * s, 10 * s], [4.0 * s, 28 * s]]},
            "shape": {"sphere": 5 * s},
            "velocity": {"cone": spread, "axis": [0, 0, 1], "speed": [300 * s, 900 * s]},
            "gravity": -980 * s, "drag": 1.5,
            "color_over_life": [[0, 1, 1, 1, 1], [0.5, 1, 0.7, 0.4, 1], [1, 0.7, 0.25, 0.08, 0]],
            "size_over_life": [[0, 1], [1, 0.5]],
            "renderer": {"type": "sprite", "material": "glow", "alignment": "velocity"},
        }],
    }


@preset("explosion: flash, fireball, sparks, debris chunks, lingering smoke and a shockwave ring (one-shot)")
def explosion(color=(1.0, 0.45, 0.12), scale=1.0, intensity=6.0, debris=True, smoke=True):
    s = scale
    emitters = [
        {"name": "Flash", "spawn": {"burst": 1}, "lifetime": 0.15, "color": _rgba((1, 0.85, 0.6), intensity),
         "size": 300 * s, "size_over_life": [[0, 0.6], [1, 1.3]], "alpha_over_life": [[0, 1], [1, 0]],
         "renderer": {"type": "sprite", "material": "glow"}},
        {"name": "Fireball", "spawn": {"burst": 30}, "lifetime": [0.5, 0.9],
         "color": _rgba(color, intensity), "size": [70 * s, 130 * s], "shape": {"sphere": 40 * s},
         "velocity": {"point": [150 * s, 450 * s]}, "drag": 4.0, "rotation": "random", "rotation_rate": [-90, 90],
         "size_over_life": [[0, 0.3], [0.3, 1], [1, 1.3]],
         "color_over_life": [[0, 1, 1, 0.85, 1], [0.4, 1, 0.5, 0.2, 0.9], [1, 0.3, 0.05, 0.02, 0]],
         "renderer": {"type": "sprite", "material": "glow"}},
        {"name": "Sparks", "spawn": {"burst": 80}, "lifetime": [0.5, 1.4], "color": _rgba((1, 0.6, 0.2), intensity * 2),
         "size": {"xy_random": [[2 * s, 10 * s], [4 * s, 30 * s]]}, "shape": {"sphere": 20 * s},
         "velocity": {"point": [600 * s, 1500 * s]}, "gravity": -980 * s, "drag": 1.2,
         "color_over_life": [[0, 1, 1, 1, 1], [1, 0.6, 0.15, 0.05, 0]],
         "renderer": {"type": "sprite", "material": "glow", "alignment": "velocity"}},
        {"name": "Shockwave", "spawn": {"burst": 1}, "lifetime": 0.35, "color": _rgba((1, 0.8, 0.6), intensity * 0.6),
         "size": 80 * s, "size_over_life": [[0, 0.3], [1, 7]], "alpha_over_life": [[0, 1], [1, 0]],
         "renderer": {"type": "sprite", "material": "shockwave"}},
    ]
    if smoke:
        emitters.append(
            {"name": "Smoke", "spawn": {"burst": 18, "time": 0.08}, "lifetime": [1.8, 3.2],
             "color": [0.14, 0.13, 0.12, 0.85], "size": [90 * s, 160 * s], "shape": {"sphere": 60 * s},
             "velocity": {"point": [60 * s, 200 * s]}, "acceleration": [0, 0, 60 * s], "drag": 2.5,
             "rotation": "random", "rotation_rate": [-25, 25], "subuv": "random",
             "size_over_life": [[0, 0.5], [1, 2.0]], "alpha_over_life": [[0, 0], [0.08, 1], [1, 0]],
             "renderer": {"type": "sprite", "material": "smoke", "subimages": [4, 4], "sort": "view_depth"}})
    if debris:
        emitters.append(
            {"name": "Debris", "spawn": {"burst": 16}, "lifetime": [1.2, 2.0], "color": [0.08, 0.07, 0.06, 1],
             "mesh_scale": [0.04 * s, 0.12 * s], "shape": {"sphere": 20 * s},
             "velocity": {"cone": 70, "axis": [0, 0, 1], "speed": [400 * s, 1000 * s]}, "gravity": -980 * s, "drag": 0.3,
             "modules": [{"stage": "particle_spawn", "module": "initial_mesh_orientation"},
                         {"stage": "particle_update", "module": "mesh_rotation_rate", "before": "Solve Forces and Velocity",
                          "inputs": {"Rotation Rate": {"random": [-2, 2]}, "Yaw": 1, "Pitch": 0.6}}],
             "renderer": {"type": "mesh", "mesh": "/Engine/BasicShapes/Cube", "material": "mesh"}})
    return {"loop": "once", "duration": 3.5, "preview": {"times": [0.04, 0.15, 0.45, 1.2], "zoom": 2.0},
            "emitters": emitters}


@preset("bullet/footstep impact: small flash, a puff of dust and a few sparks (one-shot)")
def impact(color=(0.55, 0.5, 0.45), scale=1.0, intensity=5.0, sparks_color=(1.0, 0.6, 0.25)):
    s = scale
    return {
        "loop": "once", "duration": 1.5, "preview": {"times": [0.03, 0.12, 0.35, 0.8]},
        "emitters": [
            {"name": "Flash", "spawn": {"burst": 1}, "lifetime": 0.08, "color": _rgba(sparks_color, intensity),
             "size": 40 * s, "alpha_over_life": [[0, 1], [1, 0]], "renderer": {"type": "sprite", "material": "glow"}},
            {"name": "Dust", "spawn": {"burst": 12}, "lifetime": [0.6, 1.1], "color": _rgba(color, 1, 0.8),
             "size": [20 * s, 36 * s], "shape": {"sphere": 6 * s},
             "velocity": {"cone": 60, "axis": [0, 0, 1], "speed": [80 * s, 220 * s]}, "drag": 5.0,
             "rotation": "random", "subuv": "random", "size_over_life": [[0, 0.4], [1, 1.8]],
             "alpha_over_life": [[0, 0], [0.1, 1], [1, 0]],
             "renderer": {"type": "sprite", "material": "smoke", "subimages": [4, 4]}},
            {"name": "Sparks", "spawn": {"burst": 14}, "lifetime": [0.2, 0.5], "color": _rgba(sparks_color, intensity * 2),
             "size": {"xy_random": [[1.5 * s, 6 * s], [2.5 * s, 14 * s]]},
             "velocity": {"cone": 70, "axis": [0, 0, 1], "speed": [250 * s, 600 * s]}, "gravity": -980 * s,
             "drag": 2.0, "alpha_over_life": [[0, 1], [1, 0]],
             "renderer": {"type": "sprite", "material": "glow", "alignment": "velocity"}},
        ],
    }


@preset("muzzle flash along +X: bright cone of fire and a spray of sparks (one-shot, attach to the barrel)")
def muzzle_flash(color=(1.0, 0.7, 0.3), scale=1.0, intensity=10.0):
    s = scale
    return {
        "loop": "once", "duration": 0.6, "preview": {"times": [0.02, 0.04, 0.07, 0.12], "yaw": -60, "pitch": -10},
        "emitters": [
            {"name": "Core", "spawn": {"burst": 1}, "lifetime": 0.06, "color": _rgba(color, intensity),
             "size": 40 * s, "alpha_over_life": [[0, 1], [1, 0]], "renderer": {"type": "sprite", "material": "glow"}},
            {"name": "Flame", "spawn": {"burst": 8}, "lifetime": [0.04, 0.08], "color": _rgba(color, intensity),
             "size": {"xy_random": [[8 * s, 25 * s], [14 * s, 50 * s]]},
             "velocity": {"cone": 12, "axis": [1, 0, 0], "speed": [600 * s, 1200 * s]},
             "alpha_over_life": [[0, 1], [1, 0]],
             "renderer": {"type": "sprite", "material": "glow", "alignment": "velocity"}},
            {"name": "Sparks", "spawn": {"burst": 12}, "lifetime": [0.1, 0.3], "color": _rgba(color, intensity),
             "size": {"xy_random": [[1.5 * s, 6 * s], [2.5 * s, 14 * s]]},
             "velocity": {"cone": 25, "axis": [1, 0, 0], "speed": [800 * s, 1600 * s]}, "drag": 3.0,
             "alpha_over_life": [[0, 1], [1, 0]],
             "renderer": {"type": "sprite", "material": "glow", "alignment": "velocity"}},
        ],
    }


@preset("confetti cannon: colourful paper shot upward, fluttering down (one-shot)")
def confetti(scale=1.0, count=150, spread=35):
    s = scale
    return {
        "loop": "once", "duration": 4.0, "preview": {"times": [0.1, 0.4, 1.0, 2.0]},
        "emitters": [{
            "name": "Confetti", "spawn": {"burst": count}, "lifetime": [2.5, 3.5],
            "color": {"random": [[0.05, 0.05, 0.05, 1], [1, 1, 1, 1]], "channels": "individual"},
            "size": {"xy_random": [[4 * s, 7 * s], [7 * s, 12 * s]]}, "shape": {"sphere": 5 * s},
            "velocity": {"cone": spread, "axis": [0, 0, 1], "speed": [700 * s, 1100 * s]},
            "gravity": -400 * s, "drag": 2.5, "curl_noise": {"strength": 150 * s, "frequency": 30},
            "rotation": "random", "rotation_rate": [-400, 400], "alpha_over_life": [[0, 1], [0.85, 1], [1, 0]],
            "renderer": {"type": "sprite", "material": "flat"},
        }],
    }


# --------------------------------------------------------------------------- loops

@preset("campfire flames with rising embers (looping); smoke=True adds a smoke column, light=True a flickering light")
def fire(color=(1.0, 0.42, 0.1), scale=1.0, intensity=3.0, rate=45, smoke=False, light=False):
    s = scale
    emitters = [
        {"name": "Flames", "spawn": {"rate": rate}, "lifetime": [0.6, 1.1],
         "color": _rgba(color, intensity), "size": [25 * s, 45 * s], "shape": {"sphere": 15 * s},
         "velocity": {"random": [[-10 * s, -10 * s, 70 * s], [10 * s, 10 * s, 150 * s]]},
         "curl_noise": {"strength": 60 * s, "frequency": 40}, "rotation": "random", "rotation_rate": [-60, 60],
         "size_over_life": [[0, 0.4], [0.25, 1], [1, 0.15]],
         "color_over_life": [[0, 1, 1, 1, 0], [0.12, 1, 0.9, 0.7, 1], [0.6, 1, 0.55, 0.3, 0.7], [1, 0.6, 0.15, 0.05, 0]],
         "renderer": {"type": "sprite", "material": "glow"}},
        {"name": "Embers", "spawn": {"rate": max(2, rate // 6)}, "lifetime": [1.0, 2.0],
         "color": _rgba((1, 0.5, 0.15), intensity * 3), "size": [2 * s, 4 * s], "shape": {"sphere": 15 * s},
         "velocity": {"random": [[-30 * s, -30 * s, 100 * s], [30 * s, 30 * s, 250 * s]]},
         "curl_noise": {"strength": 150 * s, "frequency": 30}, "drag": 0.5,
         "alpha_over_life": [[0, 1], [0.8, 1], [1, 0]],
         "renderer": {"type": "sprite", "material": "glow"}},
    ]
    if smoke:
        emitters.append(
            {"name": "Smoke", "spawn": {"rate": max(3, rate // 7)}, "lifetime": [2.0, 3.0],
             "color": [0.12, 0.12, 0.12, 0.6], "size": [40 * s, 70 * s], "shape": {"sphere": 20 * s},
             "velocity": {"random": [[-15 * s, -15 * s, 90 * s], [15 * s, 15 * s, 140 * s]]},
             "curl_noise": {"strength": 30 * s, "frequency": 20}, "rotation": "random", "rotation_rate": [-20, 20],
             "subuv": "random", "size_over_life": [[0, 0.6], [1, 2.5]],
             "alpha_over_life": [[0, 0], [0.3, 0.8], [1, 0]],
             "renderer": {"type": "sprite", "material": "smoke", "subimages": [4, 4], "sort": "view_depth"}})
    if light:
        emitters.append(
            {"name": "Light", "loop": "infinite", "duration": 0.15, "spawn": {"burst": 1}, "lifetime": 0.15,
             "color": {"random": [_rgba(color, intensity * 0.7), _rgba(color, intensity * 1.2)]},
             "renderer": {"type": "light", "radius": 400 * s}})
    # Big, mostly transparent smoke sprites widen the bounds the preview fits, so frame closer with smoke on.
    return {"preview": {"times": [0.5, 1.0, 1.5, 2.5], "zoom": 2.4 if smoke else 1.3}, "warmup": 1.0,
            "emitters": emitters}


@preset("soft smoke column that rises, spreads and fades (looping)")
def smoke(color=(0.5, 0.5, 0.52), scale=1.0, opacity=0.85, rate=16, rise=110):
    s = scale
    return {
        "preview": {"times": [0.5, 1.5, 3.0, 4.5], "zoom": 2.0}, "warmup": 3.0,
        "emitters": [{
            "name": "Smoke", "spawn": {"rate": rate}, "lifetime": [2.5, 4.0],
            "color": _rgba(color, 1, opacity), "size": [50 * s, 90 * s], "shape": {"sphere": 20 * s},
            "velocity": {"random": [[-15 * s, -15 * s, rise * 0.7 * s], [15 * s, 15 * s, rise * 1.3 * s]]},
            "curl_noise": {"strength": 30 * s, "frequency": 20}, "drag": 0.6,
            "rotation": "random", "rotation_rate": [-20, 20], "subuv": "random",
            "size_over_life": [[0, 0.5], [1, 2.5]], "alpha_over_life": [[0, 0], [0.15, 1], [0.7, 0.6], [1, 0]],
            "renderer": {"type": "sprite", "material": "smoke", "subimages": [4, 4], "sort": "view_depth"},
        }],
    }


@preset("magic aura: glowing motes spiral up around a ring, with twinkles (looping)")
def magic(color=(0.3, 0.6, 1.0), color2=(0.8, 0.3, 1.0), scale=1.0, intensity=6.0, radius=60, rate=60):
    s = scale
    return {
        "preview": {"times": [0.5, 1.0, 1.5, 2.5]},
        "emitters": [
            {"name": "Swirl", "spawn": {"rate": rate}, "lifetime": [1.0, 1.6],
             "color": {"random": [_rgba(color, intensity), _rgba(color2, intensity)]},
             "size": [5 * s, 10 * s], "shape": {"ring": radius * s},
             "velocity": [0, 0, 120 * s], "vortex": {"amount": 300 * s, "axis": [0, 0, 1], "pull": 150 * s},
             "curl_noise": {"strength": 20 * s, "frequency": 30},
             "size_over_life": [[0, 1], [1, 0.2]], "alpha_over_life": [[0, 0], [0.2, 1], [1, 0]],
             "renderer": {"type": "sprite", "material": "glow"}},
            {"name": "Twinkle", "spawn": {"rate": max(4, rate // 3)}, "lifetime": [0.3, 0.6],
             "color": _rgba((1, 1, 1), intensity * 1.5), "size": [8 * s, 16 * s],
             "shape": {"cylinder": [radius * 1.1 * s, 180 * s]},
             "size_over_life": [[0, 0], [0.5, 1], [1, 0]],
             "renderer": {"type": "sprite", "material": "glow"}},
        ],
    }


@preset("healing/buff effect: soft green motes rising from a disc at the feet (looping)")
def heal(color=(0.3, 1.0, 0.45), scale=1.0, intensity=5.0, radius=50, rate=45):
    s = scale
    return {
        "preview": {"times": [0.4, 0.8, 1.2, 2.0]},
        "emitters": [
            {"name": "Motes", "spawn": {"rate": rate}, "lifetime": [0.9, 1.5], "color": _rgba(color, intensity),
             "size": [5 * s, 9 * s], "shape": {"ring": radius * s, "coverage": 1.0},
             "velocity": {"random": [[0, 0, 80 * s], [0, 0, 160 * s]]}, "curl_noise": {"strength": 25 * s, "frequency": 25},
             "size_over_life": [[0, 0.5], [0.3, 1], [1, 0]], "alpha_over_life": [[0, 0], [0.2, 1], [1, 0]],
             "renderer": {"type": "sprite", "material": "glow"}},
            {"name": "Glow", "spawn": {"rate": 4}, "lifetime": 0.8, "color": _rgba(color, intensity * 0.05),
             "size": radius * 2.5 * s, "alpha_over_life": FADE_IN_OUT,
             "renderer": {"type": "sprite", "material": "glow"}},
        ],
    }


@preset("summoning circle: motes drawn into a spinning ring that spiral inward and rise (looping)")
def summon(color=(1.0, 0.35, 0.9), scale=1.0, intensity=5.0, radius=120, rate=90):
    s = scale
    return {
        "preview": {"times": [0.4, 0.8, 1.2, 2.0], "pitch": -30},
        "emitters": [
            {"name": "Spiral", "spawn": {"rate": rate}, "lifetime": [0.8, 1.2], "color": _rgba(color, intensity),
             "size": [4 * s, 9 * s], "shape": {"ring": radius * s},
             "vortex": {"amount": 500 * s, "axis": [0, 0, 1]}, "attract": {"strength": 400 * s, "radius": radius * 1.5 * s},
             "acceleration": [0, 0, 80 * s], "drag": 1.0,
             "size_over_life": [[0, 1], [1, 0]], "alpha_over_life": [[0, 0], [0.15, 1], [1, 0.2]],
             "renderer": {"type": "sprite", "material": "glow"}},
            {"name": "Rim", "spawn": {"rate": rate}, "lifetime": 0.35, "color": _rgba(color, intensity * 0.8),
             "size": [8 * s, 14 * s], "shape": {"ring": radius * s}, "alpha_over_life": [[0, 1], [1, 0]],
             "renderer": {"type": "sprite", "material": "glow"}},
        ],
    }


@preset("fountain jet of water droplets arcing under gravity (looping, GPU)")
def fountain(color=(0.55, 0.75, 1.0), scale=1.0, intensity=5.0, rate=300, height=400):
    s = scale
    speed = (2 * 980 * height) ** 0.5
    return {
        # GPU emitters have fixed bounds (generous here), so the auto-fit needs help.
        "preview": {"times": [0.3, 0.8, 1.5, 2.5], "zoom": 2.0},
        "emitters": [{
            "name": "Water", "sim": "gpu", "bounds": [[-600 * s, -600 * s, -50], [600 * s, 600 * s, height * 1.2 * s]],
            "spawn": {"rate": rate}, "lifetime": [1.6, 2.2], "color": _rgba(color, intensity, 0.8),
            "size": {"xy_random": [[3 * s, 8 * s], [6 * s, 18 * s]]}, "shape": {"sphere": 3 * s},
            "velocity": {"cone": 16, "axis": [0, 0, 1], "speed": [speed * 0.85 * s ** 0.5, speed * s ** 0.5]},
            "gravity": -980 * s, "drag": 0.1, "alpha_over_life": [[0, 1], [0.85, 1], [1, 0]],
            "renderer": {"type": "sprite", "material": "glow", "alignment": "velocity"},
        }],
    }


@preset("rain streaks falling over an area (looping, GPU; place it above the scene)")
def rain(color=(0.65, 0.72, 0.85), scale=1.0, intensity=2.5, rate=1200, area=1500, speed=1400):
    s = scale
    return {
        "preview": {"times": [0.5, 1.0, 1.5, 2.0], "zoom": 3.0, "pitch": -5}, "warmup": 2.0,
        "emitters": [{
            "name": "Rain", "sim": "gpu", "bounds": _box_bounds(area * 0.6 * s, -speed * 2.0, 50),
            "spawn": {"rate": rate}, "lifetime": [1.2, 1.6], "color": _rgba(color, intensity, 0.5),
            "size": {"xy_random": [[1.0 * s, 40 * s], [2.0 * s, 70 * s]]}, "shape": {"box": [area * s, area * s, 10]},
            "velocity": {"random": [[-20, -20, -speed], [20, 20, -speed * 0.85]]},
            "renderer": {"type": "sprite", "material": "glow", "alignment": "velocity"},
        }],
    }


@preset("snowfall drifting down over an area (looping, GPU; place it above the scene)")
def snow(color=(1.0, 1.0, 1.0), scale=1.0, intensity=2.0, rate=250, area=1500):
    s = scale
    return {
        "preview": {"times": [0.5, 1.5, 3.0, 5.0], "zoom": 2.5, "pitch": -5}, "warmup": 6.0,
        "emitters": [{
            "name": "Snow", "sim": "gpu", "bounds": _box_bounds(area * 0.6 * s, -900, 50),
            "spawn": {"rate": rate}, "lifetime": [6.0, 8.0], "color": _rgba(color, intensity, 0.9),
            "size": [3 * s, 6 * s], "shape": {"box": [area * s, area * s, 10]},
            "velocity": {"random": [[-20, -20, -110], [20, 20, -70]]},
            "curl_noise": {"strength": 40, "frequency": 10}, "alpha_over_life": [[0, 0], [0.05, 1], [0.9, 1], [1, 0]],
            "renderer": {"type": "sprite", "material": "glow"},
        }],
    }


@preset("floating dust motes in a room-sized volume (looping, GPU)")
def dust(color=(1.0, 0.92, 0.8), scale=1.0, intensity=2.0, rate=40, size=(600, 600, 300)):
    s = scale
    x, y, z = size
    return {
        "preview": {"times": [0.5, 2.0, 4.0, 6.0], "zoom": 1.5}, "warmup": 5.0,
        "emitters": [{
            "name": "Dust", "sim": "gpu", "bounds": [[-x * 0.7 * s, -y * 0.7 * s, -z * 0.7 * s], [x * 0.7 * s, y * 0.7 * s, z * 0.7 * s]],
            "spawn": {"rate": rate}, "lifetime": [4.0, 6.0], "color": _rgba(color, intensity, 0.6),
            "size": [2.0 * s, 4.0 * s], "shape": {"box": [x * s, y * s, z * s]},
            "velocity": {"random": [[-5, -5, -5], [5, 5, 8]]}, "curl_noise": {"strength": 15 * s, "frequency": 5},
            "alpha_over_life": FADE_IN_OUT, "renderer": {"type": "sprite", "material": "glow"},
        }],
    }


@preset("fireflies: a few blinking glowing bugs wandering in a volume (looping)")
def fireflies(color=(0.75, 1.0, 0.3), scale=1.0, intensity=8.0, rate=4, size=(500, 500, 200), light=False):
    s = scale
    x, y, z = size
    emitter = {
        "name": "Fireflies", "spawn": {"rate": rate}, "lifetime": [4.0, 7.0], "color": _rgba(color, intensity),
        "size": [5 * s, 8 * s], "shape": {"box": [x * s, y * s, z * s]},
        "curl_noise": {"strength": 60 * s, "frequency": 10}, "drag": 0.5,
        "alpha_over_life": [[0, 0], [0.1, 1], [0.2, 0.15], [0.35, 1], [0.5, 0.25], [0.7, 1], [1, 0]],
        "renderers": [{"type": "sprite", "material": "glow"}],
    }
    if light:
        emitter["renderers"].append({"type": "light", "radius": 150 * s})
    return {"preview": {"times": [0.5, 2.0, 4.0, 6.0], "zoom": 1.3}, "warmup": 6.0, "emitters": [emitter]}


@preset("glowing ribbon trail orbiting in a circle (looping); shows the ribbon renderer")
def orbit_trail(color=(0.3, 0.8, 1.0), scale=1.0, intensity=4.0, radius=80, width=10):
    s = scale
    return {
        "preview": {"times": [0.3, 0.6, 1.0, 1.5]},
        "emitters": [{
            "name": "Trail", "spawn": {"rate": 120}, "lifetime": 0.6, "color": _rgba(color, intensity),
            "ribbon_width": width * s, "shape": {"ring": radius * s, "inputs": {"Ring / Disc Distribution Mode": "Direct"}},
            "alpha_over_life": [[0, 1], [1, 0]],
            "modules": [{"stage": "particle_update", "module": "scale_ribbon_width",
                         "inputs": {"Ribbon Width Scale": {"curve": [[0, 1], [1, 0]]}}}],
            "renderer": {"type": "ribbon", "material": "ribbon"},
        }],
    }
