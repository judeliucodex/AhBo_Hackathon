"""Side-by-side montages: kit keyframe (left) vs sim keyframe (right), paired by order within each scenario.

Run in the ahbo-api image (Pillow):  python /scratch/montage.py <sim_dir> <out_dir>
"""
import glob
import os
import re
import sys

from PIL import Image, ImageDraw

KIT = "/repo/docs/screens/kit"
SIM = sys.argv[1]
OUT = sys.argv[2]
os.makedirs(OUT, exist_ok=True)
SCALE = float(os.environ.get("SCALE", "0.62"))
ROWS = int(os.environ.get("ROWS", "2"))


def frames(d, s):
    fs = sorted(f for f in glob.glob(f"{d}/{s}_[0-9][0-9]_*.png") if "fullpage" not in f)
    return fs


def label(path, prefix):
    b = os.path.basename(path)[:-4]
    b = re.sub(r"_(say_)?(turn|inc)_[0-9A-Z]{26}", "", b)
    b = re.sub(r"_vm_demo_\d+", "_vm", b)
    return f"{prefix}: {b}"


# skip: sim keyframes that have no kit counterpart (by name substring), per scenario
SKIP = {"S4": ["mood_warm"]}

for s in ["S1", "S2", "S3", "S4", "S4ok"]:
    kit = frames(KIT, s)
    sim_all = frames(SIM, s)
    extra = [f for f in sim_all if any(x in f for x in SKIP.get(s, []))]
    sim = [f for f in sim_all if f not in extra]
    pairs = list(zip(kit, sim))
    print(s, "kit", len(kit), "sim", len(sim), "extra", [os.path.basename(e) for e in extra])
    if len(kit) != len(sim):
        print("  !! COUNT MISMATCH", [os.path.basename(x) for x in kit], [os.path.basename(x) for x in sim])
    for i in range(0, len(pairs), ROWS):
        chunk = pairs[i:i + ROWS]
        ims = []
        for k, m in chunk:
            a, b = Image.open(k).convert("RGB"), Image.open(m).convert("RGB")
            w, h = int(a.width * SCALE), int(a.height * SCALE)
            a = a.resize((w, h), Image.LANCZOS)
            b = b.resize((w, h), Image.LANCZOS)
            ims.append((a, b, label(k, "KIT"), label(m, "SIM")))
        w, h = ims[0][0].size
        pad, bar = 8, 22
        sheet = Image.new("RGB", (2 * w + 3 * pad, len(ims) * (h + bar + pad) + pad), (40, 40, 60))
        dr = ImageDraw.Draw(sheet)
        for r, (a, b, la, lb) in enumerate(ims):
            y = pad + r * (h + bar + pad)
            dr.text((pad, y + 4), la, fill=(255, 255, 120))
            dr.text((2 * pad + w, y + 4), lb, fill=(120, 255, 255))
            sheet.paste(a, (pad, y + bar))
            sheet.paste(b, (2 * pad + w, y + bar))
        sheet.save(f"{OUT}/{s}_cmp_{i // ROWS + 1:02d}.png")
    for e in extra:
        Image.open(e).save(f"{OUT}/{s}_extra_{os.path.basename(e)}")
