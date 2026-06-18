#!/usr/bin/env python3
"""Render an assortment of random graphs and display them on the Turing 8.8" screen."""
import os
import random
import sys
from io import BytesIO

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
sys.path.insert(0, LIB)

from library.lcd.lcd_comm import Orientation
from library.lcd.lcd_comm_turing_usb import LcdCommTuringUSB

ACCENTS = ["#4cc9f0", "#f72585", "#b5e48c", "#ffd166", "#9b5de5", "#ff7b00", "#06d6a0"]


def line_plot(ax):
    x = np.linspace(0, 4 * np.pi, 200)
    for _ in range(random.randint(2, 3)):
        ax.plot(x, np.sin(x + random.uniform(0, 6)) * random.uniform(0.4, 1.2)
                + np.random.normal(0, 0.05, x.size).cumsum() * 0.04,
                color=random.choice(ACCENTS), lw=1.6)
    ax.set_title("signal", color="#cdd6f4", fontsize=9)


def scatter_plot(ax):
    n = 120
    ax.scatter(np.random.randn(n), np.random.randn(n),
               s=np.random.rand(n) * 60 + 5, c=np.random.rand(n),
               cmap="plasma", alpha=0.8, edgecolors="none")
    ax.set_title("scatter", color="#cdd6f4", fontsize=9)


def bar_plot(ax):
    cats = list("ABCDEFG")
    vals = np.random.randint(2, 20, len(cats))
    ax.bar(cats, vals, color=[random.choice(ACCENTS) for _ in cats])
    ax.set_title("bars", color="#cdd6f4", fontsize=9)


def hist_plot(ax):
    ax.hist(np.random.normal(random.uniform(-1, 1), random.uniform(0.5, 1.5), 1000),
            bins=30, color=random.choice(ACCENTS), alpha=0.85)
    ax.set_title("histogram", color="#cdd6f4", fontsize=9)


def area_plot(ax):
    x = np.arange(40)
    base = np.zeros(40)
    for _ in range(3):
        y = np.abs(np.random.randn(40)).cumsum()
        ax.fill_between(x, base, base + y, color=random.choice(ACCENTS), alpha=0.6)
        base += y
    ax.set_title("stacked area", color="#cdd6f4", fontsize=9)


def pie_plot(ax):
    vals = np.random.randint(1, 10, random.randint(3, 5))
    ax.pie(vals, colors=random.sample(ACCENTS, len(vals)),
           wedgeprops=dict(width=0.45, edgecolor="#11111b"))
    ax.set_title("donut", color="#cdd6f4", fontsize=9)


def step_plot(ax):
    y = np.random.randn(50).cumsum()
    ax.step(np.arange(50), y, color=random.choice(ACCENTS), lw=1.6, where="mid")
    ax.fill_between(np.arange(50), y.min(), y, step="mid",
                    color=random.choice(ACCENTS), alpha=0.2)
    ax.set_title("step", color="#cdd6f4", fontsize=9)


def render_figure(w, h):
    panels = [line_plot, scatter_plot, bar_plot, hist_plot, area_plot, pie_plot, step_plot]
    random.shuffle(panels)
    chosen = panels[:5]

    plt.style.use("dark_background")
    fig, axes = plt.subplots(1, 5, figsize=(w / 100, h / 100), dpi=100)
    fig.patch.set_facecolor("#080c1c")
    for ax, fn in zip(axes, chosen):
        ax.set_facecolor("#0d1326")
        fn(ax)
        ax.tick_params(colors="#6c7086", labelsize=6)
        for s in ax.spines.values():
            s.set_color("#313244")
    fig.tight_layout(pad=0.8)

    buf = BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return Image.open(buf).convert("RGB").resize((w, h))


def main():
    # Orientation from CLI: "landscape" (default) or "reverse" (180 deg)
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "landscape"
    orient = Orientation.REVERSE_LANDSCAPE if arg.startswith("rev") else Orientation.LANDSCAPE

    lcd = LcdCommTuringUSB(com_port="AUTO")
    lcd.InitializeComm()
    lcd.SetOrientation(orient)
    lcd.SetBrightness(60)

    w, h = lcd.get_width(), lcd.get_height()
    print(f"Rendering 5 random graphs on {w}x{h}")
    img = render_figure(w, h)
    lcd.DisplayPILImage(img)
    print("Displayed assortment of random graphs.")


if __name__ == "__main__":
    main()
