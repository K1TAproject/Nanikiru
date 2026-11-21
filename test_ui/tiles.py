"""Original Canvas tile faces, optionally replaced by local PNG assets."""

import math
from pathlib import Path
import tkinter as tk

ASSETS = Path(__file__).resolve().parents[1] / "assets" / "mahjong"
INK, GREEN, RED = "#25374b", "#17715b", "#b73536"
NAMES = ("一", "二", "三", "四", "五", "六", "七", "八", "九")


def tile_name(code):
    if code[1] == "z":
        return ("东", "南", "西", "北", "白", "发", "中")[int(code[0]) - 1]
    return ("赤五" if code[0] == "0" else NAMES[int(code[0]) - 1]) + {"m": "万", "p": "筒", "s": "索"}[code[1]]


def draw_face(canvas, code, x=4, y=4, w=48, h=68):
    """Draw a recognizable face without fonts containing Mahjong Unicode."""
    canvas.create_rectangle(x + 2, y + 3, x + w + 2, y + h + 3, fill="#afc3b8", outline="")
    canvas.create_rectangle(x, y, x + w, y + h, fill="#fffdf5", outline="#b9c7bd", width=1)
    color = RED if code[0] == "0" else GREEN
    number = 5 if code[0] == "0" else int(code[0])
    suit = code[1]
    cx = x + w / 2
    if suit == "m":
        canvas.create_text(cx, y + h * .30, text=NAMES[number - 1], fill=RED if code[0] == "0" else INK,
                           font=("Microsoft YaHei", int(h * .25), "bold"))
        canvas.create_text(cx, y + h * .70, text="萬", fill=RED, font=("Microsoft YaHei", int(h * .27), "bold"))
    elif suit == "z":
        if number == 5:
            canvas.create_rectangle(x + w * .23, y + h * .20, x + w * .77, y + h * .80,
                                    outline="#416a98", width=3)
        else:
            canvas.create_text(cx, y + h / 2, text=("東", "南", "西", "北", "", "發", "中")[number - 1],
                               fill=GREEN if number == 6 else RED if number == 7 else INK,
                               font=("Microsoft YaHei", int(h * .39), "bold"))
    elif suit == "s" and number == 1:
        # Stylized bird for one bamboo: body, wing, head, beak, tail and feet.
        canvas.create_oval(x + w*.30, y+h*.30, x+w*.74, y+h*.65, fill=GREEN, outline="")
        canvas.create_polygon(x+w*.45, y+h*.42, x+w*.20, y+h*.54, x+w*.54, y+h*.61, fill="#2f8d8a")
        canvas.create_oval(x+w*.51, y+h*.17, x+w*.72, y+h*.36, fill=GREEN, outline="")
        canvas.create_polygon(x+w*.69, y+h*.24, x+w*.85, y+h*.29, x+w*.69, y+h*.31, fill=RED)
        canvas.create_oval(x+w*.63, y+h*.23, x+w*.66, y+h*.26, fill="white", outline="")
        canvas.create_polygon(x+w*.35, y+h*.59, x+w*.18, y+h*.82, x+w*.51, y+h*.64, fill=GREEN)
        for fx in (.52, .64):
            canvas.create_line(x+w*fx, y+h*.63, x+w*fx, y+h*.78, fill=RED, width=2)
    else:
        layouts = {
            1: [(1, 1)], 2: [(1, 0), (1, 2)], 3: [(0, 0), (1, 1), (2, 2)],
            4: [(0, 0), (2, 0), (0, 2), (2, 2)],
            5: [(0, 0), (2, 0), (1, 1), (0, 2), (2, 2)],
            6: [(a, b) for b in range(3) for a in (0, 2)],
            7: [(1, 0)] + [(a, b) for b in (1, 2, 3) for a in (0, 2)],
            8: [(a, b) for b in range(4) for a in (0, 2)],
            9: [(a, b) for b in range(3) for a in range(3)],
        }
        rows = 4 if number in (7, 8) else 3
        for a, b in layouts[number]:
            px, py = x + w * (.23 + a * .27), y + h * (.20 + b * .60 / (rows - 1))
            if suit == "p":
                r = w * (.22 if number == 1 else .095)
                dot_color = RED if code[0] == "0" else (RED if (a, b) == (1, 1) else "#305b87")
                canvas.create_oval(px-r, py-r, px+r, py+r, outline=dot_color, width=2)
                canvas.create_oval(px-r*.35, py-r*.35, px+r*.35, py+r*.35, fill=dot_color, outline="")
            else:
                length = h * (.14 if rows == 3 else .10)
                canvas.create_line(px, py-length/2, px, py+length/2, fill=color, width=4)
                canvas.create_line(px-3, py, px+3, py, fill=color, width=2)
    if code[0] == "0":
        canvas.create_oval(x+3, y+3, x+7, y+7, fill=RED, outline="")


class TileButton(tk.Canvas):
    def __init__(self, parent, code, command=None, *, small=False):
        self.face_width, self.face_height = (34, 48) if small else (48, 68)
        super().__init__(parent, width=self.face_width+10, height=self.face_height+10,
                         bg=parent.cget("bg"), highlightthickness=2, highlightbackground=parent.cget("bg"),
                         takefocus=bool(command), cursor="hand2" if command else "")
        path = ASSETS / f"{code}.png"
        self.photo = None
        if path.exists():
            try:
                source = tk.PhotoImage(master=self, file=str(path))
                factor = max(1, math.ceil(source.width()/self.face_width), math.ceil(source.height()/self.face_height))
                self.photo = source.subsample(factor)
            except tk.TclError:
                pass  # A broken optional image must not prevent testing a position.
        if self.photo is not None:
            self.create_image((self.face_width+10)/2, (self.face_height+10)/2, image=self.photo)
        else:
            draw_face(self, code, w=self.face_width, h=self.face_height)
        self.enabled = True
        if command:
            def activate(event):
                if self.enabled:
                    self.focus_set()
                    command()
            self.bind("<Button-1>", activate)
            self.bind("<Return>", activate)
            self.bind("<space>", activate)
        self.code = code

    def select(self, selected):
        self.configure(highlightbackground="#d69b35" if selected else self.master.cget("bg"),
                       highlightcolor="#d69b35" if selected else "#799ba7")

    def enable(self, enabled):
        self.enabled = enabled
        self.delete("disabled")
        self.configure(cursor="hand2" if enabled else "", takefocus=enabled)
        if not enabled:
            self.create_rectangle(0, 0, self.face_width+10, self.face_height+10,
                                  fill=self.master.cget("bg"), outline="", stipple="gray50", tags="disabled")
