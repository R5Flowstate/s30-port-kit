"""Procedural 1920x1080 loadscreen art (own art, no retail textures). Usage: make_loadscreen_png.py "<TITLE>" "<subtitle>" <out.png>"""
import sys
from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
img = Image.new("RGB", (W, H))
px = img.load()
for y in range(H):
    for x in range(W):
        t = y / H
        u = x / W
        r = int(18 + 40 * t + 30 * u * (1 - t))
        g = int(20 + 30 * t + 18 * u * (1 - t))
        b = int(28 + 22 * t)
        px[x, y] = (r, g, b)
d = ImageDraw.Draw(img)
for i, c in enumerate([(255, 170, 90), (120, 170, 255), (190, 190, 200), (90, 110, 200)]):
    x0 = 160 + i * 70
    d.rectangle([x0, 760, x0 + 50, 810], fill=c)
try:
    big = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 110)
    small = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 44)
except OSError:
    big = small = ImageFont.load_default()
d.text((160, 520), sys.argv[1], font=big, fill=(235, 235, 240))
d.text((164, 670), sys.argv[2], font=small, fill=(170, 175, 190))
img.save(sys.argv[3])
print("ok")
