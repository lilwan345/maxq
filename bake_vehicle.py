#!/usr/bin/env python3
"""Bake high-detail vehicle sprites for MAX-Q — pure code, no AI images.

Renders assets/vehicle/stack.png and booster.png at 12 px/unit (the
procedural drawing's units: booster 22u wide x 78u tall, ship +56u) with
layered materials: stainless cylinder shading, brushed-roll noise, weld
seams, heat-shield tile grid, grid-fin waffles, engine skirt + raptor
bells, soot/scorch, section AO. The in-app plume stays procedural.

Run:  /opt/anaconda3/bin/python3 bake_vehicle.py
"""

import numpy as np
from PIL import Image
from pathlib import Path

U = 12                       # px per unit
# Proportions from the reference spec pass: slenderness 10.5:1,
# booster:ship = 1.24, big flaps, bells breaking the bottom silhouette.
BW, BH, SH = 22, 128, 103    # booster width/height, ship height (units)
FLAP = 8.7                   # aft-flap overhang (units)
BELLS = 2.0                  # how far engine bells poke below the base
MARGIN = 1.0

rng = np.random.default_rng(7)


def upx(u):
    return int(round(u * U))


def smooth1d(a, k):
    ker = np.ones(k) / k
    return np.convolve(a, ker, mode="same")


def cylinder_profile(w, spec_pos=0.30, spec_w=0.085, base=0.62):
    """Horizontal brightness profile of a left-lit steel cylinder."""
    t = np.linspace(0, 1, w)
    b = base + 0.10 * np.cos((t - 0.42) * np.pi)          # body roundness
    b += 0.55 * np.exp(-0.5 * ((t - spec_pos) / spec_w) ** 2)   # key spec
    b += 0.16 * np.exp(-0.5 * ((t - 0.62) / 0.16) ** 2)   # soft fill band
    b += 0.10 * np.exp(-0.5 * ((t - 0.97) / 0.05) ** 2)   # rim bounce
    b -= 0.18 * np.exp(-0.5 * ((t - 0.015) / 0.04) ** 2)  # left edge dark
    return np.clip(b, 0.05, 1.6)


def steel_rgb(shade):
    """Map brightness 0..1.6 -> cool stainless RGB."""
    r = np.clip(shade * 168 + 26, 0, 255)
    g = np.clip(shade * 172 + 30, 0, 255)
    b = np.clip(shade * 180 + 38, 0, 255)
    return np.stack([r, g, b], axis=-1)


def brushed(h, w, row_amp=0.045, col_amp=0.06):
    """Brushed-roll texture: horizontal ring jitter + vertical streaks."""
    rows = smooth1d(rng.normal(0, 1, h), 5) * row_amp
    cols = smooth1d(rng.normal(0, 1, w), 9) * col_amp
    return 1.0 + rows[:, None] + cols[None, :]


def build(mode="stack"):
    """mode: 'stack' (booster+ship) | 'booster' | 'ship' (alone, for the
    post-separation orbital coast)."""
    has_booster = mode in ("stack", "booster")
    has_ship = mode in ("stack", "ship")
    wpx = upx(BW + 2 * FLAP + 2 * MARGIN)
    hpx = upx((BH if has_booster else 0) + (SH if has_ship else 0)
              + 2 * MARGIN + BELLS)
    img = np.zeros((hpx, wpx, 4), dtype=float)
    x0 = upx(FLAP + MARGIN)                  # body left edge
    x1 = x0 + upx(BW)
    base_y = hpx - upx(MARGIN + BELLS)       # bottom of engine skirt
    b_top = base_y - upx(BH) if has_booster else base_y
    cw = x1 - x0
    prof = cylinder_profile(cw)
    TILE = (30, 32, 42)
    tile = upx(1.05)

    def bells_below(by):
        """Raptor bells poking BELOW the silhouette (the reference tell)."""
        bell_w = int(cw / 5.5)
        for k, fx in enumerate((0.14, 0.38, 0.62, 0.86)):
            bx = x0 + int(cw * fx)
            for dy in range(upx(BELLS)):
                yy = by + dy
                if yy >= hpx:
                    break
                half = int(bell_w * (0.50 - 0.16 * dy / upx(BELLS)))
                if half < 1:
                    continue
                img[yy, bx - half:bx + half, :3] = (40, 38, 36)
                img[yy, bx - half:bx + half, 3] = 255
                img[yy, bx - half:bx - half + 2, :3] = (92, 90, 86)

    if has_booster:
        # ---------------- booster barrel
        barrel_h = upx(BH)
        shade = np.tile(prof, (barrel_h, 1)) * brushed(barrel_h, cw)
        img[b_top:base_y, x0:x1, :3] = steel_rgb(shade)
        img[b_top:base_y, x0:x1, 3] = 255
        for i in range(1, 8):                        # weld seams
            y = b_top + int(barrel_h * i / 8)
            img[y - 1:y + 1, x0:x1, :3] *= 0.74
            img[y + 1:y + 2, x0:x1, :3] = np.clip(
                img[y + 1:y + 2, x0:x1, :3] * 1.16, 0, 255)
        rx = x0 + int(cw * 0.74)                     # cable raceway
        img[b_top + upx(8):base_y - upx(11), rx:rx + upx(0.9), :3] *= 0.66
        img[b_top + upx(8):base_y - upx(11),
            rx + upx(0.9):rx + upx(1.1), :3] *= 1.1

        # soot near the base
        soot_h = upx(16)
        soot = np.linspace(0, 1, soot_h)[:, None] ** 1.6
        streak = 0.55 + 0.45 * smooth1d(rng.random(cw), 7)[None, :]
        img[base_y - soot_h:base_y, x0:x1, :3] *= \
            (1.0 - 0.45 * soot * streak)[..., None]

        # HOT-STAGE INTERSTAGE: near-black ring with vent slits — the
        # single strongest Starship tell (top of the booster)
        ring_h = upx(5)
        img[b_top:b_top + ring_h, x0:x1, :3] = (44, 45, 49)
        for k in range(7):
            sx_ = x0 + int(cw * (k + 0.5) / 7)
            img[b_top + upx(1.4):b_top + ring_h - upx(1.4),
                sx_:sx_ + upx(0.7), :3] = (95, 95, 100)

        # grid fins — compact dark nubs right below the interstage
        fin_w, fin_h = upx(6.6), upx(6.4)
        fin_y = b_top + ring_h + upx(0.6)
        for fx in (x0 - fin_w + upx(0.6), x1 - upx(0.6)):
            plate = np.full((fin_h, fin_w, 3), 50.0)
            plate[:, :, 1] += 2; plate[:, :, 2] += 8
            plate[::upx(0.9), :, :] = 90
            plate[:, ::upx(0.9), :] = 90
            plate[:, :2, :] = 118
            img[fin_y:fin_y + fin_h, fx:fx + fin_w, :3] = plate
            img[fin_y:fin_y + fin_h, fx:fx + fin_w, 3] = 255

        # engine skirt + catch pins + bells breaking the silhouette
        sk_h = upx(10)
        img[base_y - sk_h:base_y, x0:x1, :3] *= 0.52
        for px_ in (x0 - upx(0.9), x1):              # catch-pin tabs
            img[base_y - sk_h - upx(1.5):base_y - sk_h + upx(0.5),
                int(px_):int(px_) + upx(0.9), :3] = (52, 54, 60)
            img[base_y - sk_h - upx(1.5):base_y - sk_h + upx(0.5),
                int(px_):int(px_) + upx(0.9), 3] = 255
        bells_below(base_y)

    if has_ship:
        # ---------------- ship barrel + ogive nose
        s_bot = b_top
        s_top = s_bot - upx(SH)
        nose_h = upx(26)
        body_h = upx(SH) - nose_h
        # The SHIP is FULLY tiled on the camera side — glossy black with a
        # cylinder sheen (the in-flight tile-side-on look of the real one).
        def tile_row(yy, xs_, xe_, sheen_prof=None):
            w_ = xe_ - xs_
            if w_ < 1:
                return
            xi = np.arange(xs_, xe_)
            base = 34 + ((xi + yy) * 7) % 13 - 6
            grout = ((xi % tile) < 1) | ((yy % tile) < 1)
            v_ = np.where(grout, 18, base).astype(float)
            if sheen_prof is not None:
                v_ = v_ * (0.62 + 0.85 * sheen_prof)
            img[yy, xs_:xe_, 0] = v_
            img[yy, xs_:xe_, 1] = v_ + 2
            img[yy, xs_:xe_, 2] = v_ + 9
            img[yy, xs_:xe_, 3] = 255

        body_prof = cylinder_profile(cw)
        for yy in range(s_top + nose_h, s_bot):
            tile_row(yy, x0, x1, body_prof)
        # ogive — sharper, fully tiled
        for row in range(nose_h):
            v = row / nose_h
            half = int(cw / 2 * (v ** 0.75))
            cx_ = (x0 + x1) // 2
            if half < 1:
                continue
            pr = cylinder_profile(2 * half)
            tile_row(s_top + row, cx_ - half, cx_ + half, pr)
        for i in range(1, 5):                        # faint tile-line rings
            y = s_top + nose_h + int(body_h * i / 5)
            img[y - 1:y + 1, x0:x1, :3] *= 0.82

        # flaps — BOTH sides dark tiles. Forward pair: long (36% of ship),
        # widest near the tip, tapering into the body. Aft pair: bottom
        # flush with the interstage, widening downward.
        def flap(fy, fh, ovh, taper_down):
            for side in (0, 1):
                for ry in range(fh):
                    v = ry / fh
                    frac = (1.0 - 0.75 * v) if taper_down else (0.40 + 0.60 * v)
                    wid = max(2, int(ovh * frac))
                    yy = fy + ry
                    if side == 0:
                        xs_, xe_ = x0 - wid, x0
                    else:
                        xs_, xe_ = x1, x1 + wid
                    dark = 28 + (yy // tile % 2) * 6
                    img[yy, xs_:xe_, :3] = (dark, dark + 2, dark + 10)
                    img[yy, xs_:xe_, 3] = 255
                # hinge AO
                if side == 0:
                    img[fy:fy + fh, x0:x0 + 2, :3] *= 0.85
                else:
                    img[fy:fy + fh, x1 - 2:x1, :3] *= 0.85

        # forward flaps: triangles hinged ON the ogive surface, zero width
        # at the top, sweeping DOWN-and-out (the real geometry)
        ffy, ffh, fovh = s_top + upx(15), upx(19), upx(5.2)
        cx_ = (x0 + x1) // 2
        for ry in range(ffh):
            yy = ffy + ry
            v = ry / ffh
            row = yy - s_top
            if row < nose_h:
                half = int(cw / 2 * ((row / nose_h) ** 0.75))
            else:
                half = cw // 2
            wid = max(1, int(fovh * v))
            dark = 26 + (yy // tile % 2) * 6
            img[yy, cx_ - half - wid:cx_ - half, :3] = (dark, dark + 2,
                                                        dark + 10)
            img[yy, cx_ - half - wid:cx_ - half, 3] = 255
            img[yy, cx_ + half:cx_ + half + wid, :3] = (dark, dark + 2,
                                                        dark + 10)
            img[yy, cx_ + half:cx_ + half + wid, 3] = 255
        flap(s_bot - upx(24), upx(24), upx(FLAP), taper_down=False)

        # ship-only: engine band + bells
        if not has_booster:
            img[base_y - upx(3):base_y, x0:x1, :3] *= 0.55
            bells_below(base_y)

    out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    return out


def fbm(h, w, octs=((6, 1.0), (12, 0.5), (24, 0.26), (48, 0.13)), seed=3):
    """Fractal value noise via stacked bilinear-upsampled random grids."""
    from scipy.ndimage import zoom
    r = np.random.default_rng(seed)
    out = np.zeros((h, w))
    for cells, amp in octs:
        g = r.random((cells, int(cells * w / h) + 2))
        z = zoom(g, (h / g.shape[0], w / g.shape[1]), order=1)[:h, :w]
        out += amp * z
    out -= out.min()
    out /= out.max()
    return out


def _wrap_blend(a, n=160):
    """Crossfade the strip's left edge into its right edge → seamless scroll."""
    w = np.linspace(0, 1, n)
    if a.ndim == 3:
        w = w[None, :, None]
    else:
        w = w[None, :]
    a[:, :n] = a[:, :n] * w + a[:, -n:] * (1 - w)
    return a


def make_earth(out):
    """Orbital surface strip: ocean, fractal continents, turquoise coasts.
    Stylized-soft per the reference pass: lower contrast, blurred detail."""
    from scipy.ndimage import binary_dilation, gaussian_filter
    H, W = 300, 1400
    base = fbm(H, W, seed=11)
    landm = fbm(H, W, octs=((4, 1), (9, .55), (20, .3), (40, .15)), seed=23)
    land = landm > 0.60
    coast = binary_dilation(land, np.ones((7, 7))) & ~land
    img = np.zeros((H, W, 3))
    img[:] = np.stack([46 + 13 * base, 90 + 20 * base, 142 + 24 * base], -1)
    landtex = fbm(H, W, seed=31)
    lc = np.stack([138 + 50 * landtex, 126 + 38 * landtex,
                   90 + 24 * landtex], -1)
    img[land] = lc[land]
    img[coast] = img[coast] * 0.5 + np.array([74, 150, 168]) * 0.5
    img = gaussian_filter(img, sigma=(1.6, 1.6, 0))
    img = _wrap_blend(np.clip(img, 0, 255))
    rgba = np.dstack([img, np.full((H, W), 255.0)])
    Image.fromarray(rgba.astype(np.uint8)).save(out / "earth.png")


def make_clouds(out, name, seed, octs, thresh, gain):
    """Soft white cloud masses with blurred fractal alpha (two layers
    scroll at different speeds in-app)."""
    from scipy.ndimage import gaussian_filter
    H, W = 300, 1400
    c = fbm(H, W, octs=octs, seed=seed)
    a = np.clip((c - thresh) * gain, 0, 1)
    a = gaussian_filter(a, sigma=2.2)
    a = _wrap_blend(a)
    rgba = np.dstack([np.full((H, W), 248.0)] * 3 + [a * 215])
    Image.fromarray(rgba.astype(np.uint8)).save(out / name)


def make_tower(out):
    """Mechazilla bake: a proper lattice mast with depth (back chords
    darker), X-bracing, a top crane boom reaching LEFT with a hook, and an
    antenna mast — measured from the reference card. Chopstick arms are NOT
    baked (the app animates them for the catch). Lattice is centered in the
    canvas so the app's bottom-center anchor stays on the mast centerline."""
    from PIL import ImageDraw
    TW = 24            # lattice width (units)
    TH = 224           # lattice height
    BOOM = 40          # crane boom reach (left)
    PAD = BOOM + 2     # symmetric canvas padding
    W = upx(TW + 2 * PAD)
    H = upx(TH + 16)   # crane structure + mast above the lattice top
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cxp = W // 2
    xl, xr = cxp - upx(TW) // 2, cxp + upx(TW) // 2
    top = H - upx(TH)
    base = H

    BACK = (36, 39, 47, 255)
    FRONT = (58, 63, 74, 255)
    EDGE = (90, 97, 110, 255)
    DECK = (70, 75, 86, 255)

    # back chord pair (depth) + back bracing
    bxl, bxr = cxp - upx(TW * 0.30), cxp + upx(TW * 0.30)
    for x in (bxl, bxr):
        d.line([(x, top + upx(2)), (x, base)], fill=BACK, width=upx(1.6))
    y = top + upx(2)
    while y < base - upx(8):
        d.line([(bxl, y), (bxr, y + upx(9))], fill=BACK, width=upx(0.8))
        d.line([(bxr, y), (bxl, y + upx(9))], fill=BACK, width=upx(0.8))
        y += upx(9)

    # front chord pair + bracing + horizontals
    for x in (xl, xr):
        d.line([(x, top), (x, base)], fill=FRONT, width=upx(2.2))
        d.line([(x - upx(0.9), top), (x - upx(0.9), base)],
               fill=EDGE, width=upx(0.5))
    y = top
    while y < base - upx(8):
        d.line([(xl, y), (xr, y + upx(9))], fill=FRONT, width=upx(1.0))
        d.line([(xr, y), (xl, y + upx(9))], fill=FRONT, width=upx(1.0))
        d.line([(xl, y), (xr, y)], fill=FRONT, width=upx(0.8))
        y += upx(9)
    d.line([(xl, base - 2), (xr, base - 2)], fill=FRONT, width=upx(1.2))

    # top deck + antenna mast
    d.rectangle([xl - upx(2), top - upx(3), xr + upx(2), top], fill=DECK)
    d.line([(cxp + upx(4), top - upx(3)), (cxp + upx(4), top - upx(11))],
           fill=FRONT, width=upx(0.9))
    d.ellipse([cxp + upx(3.4), top - upx(12), cxp + upx(4.6), top - upx(10.8)],
              fill=(200, 60, 50, 255))

    # crane boom reaching LEFT + counterjib stub right
    by0, by1 = top - upx(2.5), top + upx(4)
    bx_end = xl - upx(BOOM)
    d.line([(xl, by0), (bx_end, by0)], fill=FRONT, width=upx(1.4))
    d.line([(xl, by1), (bx_end + upx(2), by1)], fill=FRONT, width=upx(1.4))
    x = xl - upx(3)
    while x > bx_end + upx(2):
        d.line([(x, by0), (x - upx(4), by1)], fill=FRONT, width=upx(0.8))
        d.line([(x - upx(4), by0), (x, by1)], fill=FRONT, width=upx(0.8))
        x -= upx(4)
    d.rectangle([xr, by0, xr + upx(8), by1], fill=DECK)        # counterjib
    # hook trolley + cable
    d.rectangle([bx_end + upx(1), by1, bx_end + upx(4), by1 + upx(3)],
                fill=DECK)
    d.line([(bx_end + upx(2.5), by1 + upx(3)),
            (bx_end + upx(2.5), by1 + upx(9))], fill=EDGE, width=upx(0.5))

    im.save(out / "tower.png")
    night_variant(im).save(out / "tower_night.png")


def night_variant(im):
    """Pre-darkened blue-shifted copy; the app crossfades to it as the
    palette's ambient level drops (sprites don't react to `amb` otherwise)."""
    a = np.asarray(im).astype(float)
    a[..., 0] *= 0.30
    a[..., 1] *= 0.34
    a[..., 2] *= 0.46
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def main():
    out = Path(__file__).resolve().parent / "assets" / "vehicle"
    out.mkdir(parents=True, exist_ok=True)
    for mode in ("stack", "booster", "ship"):
        im = build(mode)
        im.save(out / f"{mode}.png")
        night_variant(im).save(out / f"{mode}_night.png")
    make_tower(out)
    make_earth(out)
    make_clouds(out, "clouds.png", seed=41, thresh=0.58, gain=3.2,
                octs=((8, 1), (18, .5), (40, .3), (80, .18)))
    make_clouds(out, "clouds2.png", seed=57, thresh=0.52, gain=2.2,
                octs=((3, 1), (7, .6), (14, .3)))
    print("baked:", sorted(p.name for p in out.glob('*.png')))


if __name__ == "__main__":
    main()
