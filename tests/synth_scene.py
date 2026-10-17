"""Synthetic game scenes for the vision tests: a textured world wider than the view,
made-up monster sprites (not game art), a camera that can scroll, and optional damage
numbers. Each monster is a dict: {"sprite", "x", "y", "born", "dies", "vx", "damage": [t...]}.
"""
import cv2
import numpy as np


def make_world(h=400, w=1800, seed=1):
    rng = np.random.default_rng(seed)
    world = cv2.GaussianBlur(rng.integers(0, 255, (h, w, 3), dtype=np.uint8), (0, 0), 3)
    cv2.rectangle(world, (0, h - 60), (w, h), (60, 90, 120), -1)          # a "ground" band
    return world


def make_sprite(color=(40, 200, 90), size=(30, 26), eye=True, seed=0):
    h, w = size
    spr = np.zeros((h, w, 4), np.uint8)
    cv2.ellipse(spr, (w // 2, h // 2), (w // 2 - 1, h // 2 - 1), 0, 0, 360, (*color, 255), -1)
    if eye:
        cv2.circle(spr, (w // 3, h // 3), max(2, w // 9), (0, 0, 0, 255), -1)
    rng = np.random.default_rng(seed)
    noise = rng.integers(-25, 25, (h, w, 3))
    spr[..., :3] = np.clip(spr[..., :3].astype(int) + noise * (spr[..., 3:] > 0), 0, 255).astype(np.uint8)
    return spr


def paste(frame, spr, x, y, flip=False):
    """Alpha-paste a BGRA sprite with its top-left at (x, y); clipped at the edges."""
    if flip:
        spr = spr[:, ::-1]
    h, w = spr.shape[:2]
    H, W = frame.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    s = spr[y0 - y:y1 - y, x0 - x:x1 - x]
    a = s[..., 3:] / 255.0
    frame[y0:y1, x0:x1] = (s[..., :3] * a + frame[y0:y1, x0:x1] * (1 - a)).astype(np.uint8)


def damage_digits(frame, x, y, scale=0.6):
    """An orange damage number centred at (x, y)."""
    cv2.putText(frame, "123", (int(x) - 18, int(y)), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 120, 255), 2)


def render(world, cam_x, view_w, monsters, t, cam_y=0, view_h=None):
    view_h = view_h or world.shape[0]
    f = world[cam_y:cam_y + view_h, cam_x:cam_x + view_w].copy()
    for m in monsters:
        if not (m["born"] <= t < m.get("dies", 1e9)):
            continue
        wx = m["x"] + m.get("vx", 0) * (t - m["born"])
        sx, sy = int(wx - cam_x), int(m["y"] - cam_y)
        paste(f, m["sprite"], sx, sy, flip=m.get("vx", 0) > 0)
        for d in m.get("damage", ()):
            if d <= t < d + 0.6:                       # numbers float up for a moment
                h, w = m["sprite"].shape[:2]
                damage_digits(f, sx + w / 2, sy - 8 - 30 * (t - d))
    return f
