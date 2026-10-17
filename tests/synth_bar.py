"""Synthetic status bars, built from the game-font digit examples in assets/digits.npz:
'HP[cur/max]  MP[cur/max]  EXP 12,345 [12.34%]' at any size, so EXP/HP/MP reading and
the video analyzer can be tested with numbers that change, without game footage.
"""
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
_D = np.load(ROOT / "assets" / "digits.npz")
GLYPHS = {k[1:]: _D[k][0] for k in _D.files if k.startswith("d")}       # 28 x 19 at height 24
GREEN = (30, 230, 177)          # BGR with OpenCV hue ~38: the brackets' yellow-green


def _digit(img, ch, cx, cy, gh):
    p = GLYPHS[ch].astype(np.float32)
    k = gh / 24.0
    h, w = int(round(p.shape[0] * k)), int(round(p.shape[1] * k))
    p = cv2.resize(p, (max(1, w), max(1, h)), interpolation=cv2.INTER_LINEAR)
    x0, y0 = int(round(cx - w / 2)), int(round(cy - h / 2))
    region = img[y0:y0 + h, x0:x0 + w]
    p = p[:region.shape[0], :region.shape[1]]
    region[:] = np.maximum(region, np.clip(p, 0, 255)[..., None].astype(np.uint8))


def _bracket(img, x, cy, gh, left=True):
    t = max(1, int(round(0.18 * gh)))
    top, bot = int(cy - gh / 2), int(cy + gh / 2)
    stub = max(2, int(round(0.32 * gh)))
    if left:
        cv2.rectangle(img, (x, top), (x + t - 1, bot), GREEN, -1)
        cv2.rectangle(img, (x, top), (x + stub, top + t - 1), GREEN, -1)
        cv2.rectangle(img, (x, bot - t + 1), (x + stub, bot), GREEN, -1)
        return x + stub + 1
    cv2.rectangle(img, (x + stub - t + 1, top), (x + stub, bot), GREEN, -1)
    cv2.rectangle(img, (x, top), (x + stub, top + t - 1), GREEN, -1)
    cv2.rectangle(img, (x, bot - t + 1), (x + stub, bot), GREEN, -1)
    return x + stub + 1


def _text(img, text, x, cy, gh):
    """Draws digits and , . / % starting at x; returns the x after the text."""
    pitch = 0.84 * gh
    for ch in text:
        if ch.isdigit():
            _digit(img, ch, x + pitch / 2, cy, gh)
            x += pitch
        elif ch in ",.":
            s = max(1, int(round(0.18 * gh)))
            cv2.rectangle(img, (int(x + 0.1 * gh), int(cy + gh / 2 - s)), (int(x + 0.1 * gh) + s, int(cy + gh / 2)),
                          (230, 230, 230), -1)
            x += 0.4 * gh
        elif ch == "/":
            cv2.line(img, (int(x + 0.6 * gh), int(cy - 0.41 * gh)), (int(x + 0.1 * gh), int(cy + 0.41 * gh)),
                     (235, 235, 235), max(1, int(round(0.14 * gh))))
            x += pitch
        elif ch == "%":
            x += 0.2 * gh
            w = int(round(0.9 * gh))
            cv2.rectangle(img, (int(x), int(cy - gh / 2)), (int(x) + w, int(cy + gh / 2)), (235, 235, 235),
                          max(1, int(round(0.15 * gh))))
            cv2.line(img, (int(x), int(cy + gh / 2)), (int(x) + w, int(cy - gh / 2)), (235, 235, 235),
                     max(1, int(round(0.15 * gh))))
            x += w + 0.15 * gh
        x += 0.0
    return int(round(x))


def status_bar(exp, pct, hp=None, mp=None, gh=14, width=None, bg=(40, 36, 34)):
    """BGR image of a status line. pct is the % shown (2 decimals)."""
    height = int(3 * gh)
    width = width or int(70 * gh)
    img = np.full((height, width, 3), bg, np.uint8)
    cy = height / 2
    x = int(2 * gh)
    for gauge in (hp, mp):
        if gauge is None:
            x += int(14 * gh)
            continue
        x = _bracket(img, x, cy, gh, True) + int(0.2 * gh)
        x = _text(img, f"{gauge[0]}/{gauge[1]}", x, cy, gh)
        x = _bracket(img, x + int(0.1 * gh), cy, gh, False) + int(4 * gh)
    x += int(2 * gh)
    x = _text(img, f"{exp:,}", x, cy, gh) + int(0.35 * gh)
    x = _bracket(img, x, cy, gh, True) + int(0.2 * gh)
    x = _text(img, f"{pct:.2f}%", x, cy, gh)
    _bracket(img, x + int(0.1 * gh), cy, gh, False)
    return img
