"""
ED Cockpit — Activity Bar
Horizontal row of 7 icon buttons, one per in-game activity.

Run:      python3 activity_bar.py
Requires: Python 3 standard library only (tkinter)
Icons are embedded as base64 GIF data in icons_b64.py — no external files needed.
"""
import base64
import tkinter as tk
from tkinter import ttk
import icons_b64

# ---------------------------------------------------------------------------
# Activity definitions  (b64 attribute name, display label)
# ---------------------------------------------------------------------------
ACTIVITIES = [
    (icons_b64.EXOBIOLOGY,    "Exobiology"),
    (icons_b64.MINING,        "Mining"),
    (icons_b64.NAVIGATION,    "Navigation"),
    (icons_b64.DEEP_SPACE,    "Deep Space"),
    (icons_b64.TRADING,       "Trading"),
    (icons_b64.COMBAT,        "Combat"),
    (icons_b64.FLEET_CARRIER, "Fleet Carrier"),
    (icons_b64.STATS_STATUS,  "Statistics"),
]

# ---------------------------------------------------------------------------
# Colours (match the icon dark theme)
# ---------------------------------------------------------------------------
BG          = "#0d0d1e"
BTN_NORMAL  = "#14142e"
BTN_HOVER   = "#1e2a4a"
BTN_ACTIVE  = "#0a1428"
BORDER_IDLE = "#2a2a4a"
BORDER_HOV  = "#4da6ff"
LABEL_FG    = "#7ab8e8"
LABEL_SEL   = "#4da6ff"
FONT        = ("Consolas", 8)
FONT_SEL    = ("Consolas", 8, "bold")

# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------
class ActivityBar(tk.Frame):
    """
    Horizontal strip of icon buttons.
    Selected button is highlighted; hover shows a blue border.
    Clicking triggers the on_select callback with the activity name.
    """

    def __init__(self, parent, on_select=None, **kwargs):
        super().__init__(parent, bg=BG, **kwargs)
        self._on_select  = on_select or (lambda name: None)
        self._images     = []   # PhotoImage refs — must survive GC
        self._btns       = []   # (border_frame, btn, label) per activity
        self._selected   = None
        self._build()

    # ------------------------------------------------------------------
    def _build(self):
        for idx, (img_data, label_text) in enumerate(ACTIVITIES):
            # Decode base64 GIF directly into a PhotoImage — no Pillow needed
            try:
                tk_img = tk.PhotoImage(data=img_data)
            except Exception as e:
                print(f"Warning: could not load icon for {label_text!r}: {e}")
                tk_img = None
            self._images.append(tk_img)

            # Outer frame acts as the coloured border
            border = tk.Frame(self, bg=BORDER_IDLE, padx=2, pady=2)
            border.grid(row=0, column=idx, padx=5, pady=6)

            # Inner button frame
            btn_frame = tk.Frame(border, bg=BTN_NORMAL)
            btn_frame.pack()

            btn = tk.Button(
                btn_frame,
                image=tk_img,
                bg=BTN_NORMAL,
                activebackground=BTN_ACTIVE,
                relief="flat",
                bd=0,
                cursor="hand2",
                command=lambda i=idx, n=label_text: self._click(i, n),
            )  # type: ignore[arg-type]
            btn.pack(padx=1, pady=1)

            lbl = tk.Label(
                self,
                text=label_text,
                bg=BG,
                fg=LABEL_FG,
                font=FONT,
            )
            lbl.grid(row=1, column=idx, pady=(0, 4))

            # Hover bindings (button + label both trigger)
            for widget in (btn, btn_frame, lbl):
                widget.bind("<Enter>", lambda e, i=idx: self._hover_on(i))
                widget.bind("<Leave>", lambda e, i=idx: self._hover_off(i))

            self._btns.append((border, btn, lbl))

    # ------------------------------------------------------------------
    def _hover_on(self, idx):
        border, btn, _ = self._btns[idx]
        if idx != self._selected:
            border.configure(bg=BORDER_HOV)
        btn.configure(bg=BTN_HOVER)
        btn.master.configure(bg=BTN_HOVER)

    def _hover_off(self, idx):
        border, btn, _ = self._btns[idx]
        if idx != self._selected:
            border.configure(bg=BORDER_IDLE)
        btn.configure(bg=BTN_NORMAL)
        btn.master.configure(bg=BTN_NORMAL)

    # ------------------------------------------------------------------
    def _click(self, idx, name):
        # Deselect previous
        if self._selected is not None:
            old_border, _, old_lbl = self._btns[self._selected]
            old_border.configure(bg=BORDER_IDLE)
            old_lbl.configure(fg=LABEL_FG, font=FONT)

        # Select new
        self._selected = idx
        border, _, lbl = self._btns[idx]
        border.configure(bg=BORDER_HOV)
        lbl.configure(fg=LABEL_SEL, font=FONT_SEL)

        self._on_select(name)

    # ------------------------------------------------------------------
    def select(self, idx: int):
        """Programmatically select a button by index."""
        if 0 <= idx < len(self._btns):
            self._click(idx, ACTIVITIES[idx][1])


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------
def main():
    root = tk.Tk()
    root.title("ED Cockpit — Activity Bar")
    root.configure(bg=BG)
    root.resizable(False, False)

    # Status label to show which activity is selected
    status = tk.Label(
        root,
        text="Select an activity",
        bg=BG,
        fg="#555577",
        font=("Consolas", 9, "italic"),
    )
    status.pack(pady=(8, 0))

    def on_activity(name):
        status.configure(text=f"Active: {name}", fg=LABEL_SEL)
        print(f"Activity selected: {name}")

    bar = ActivityBar(root, on_select=on_activity)
    bar.pack(padx=10, pady=(4, 10))

    # Pre-select first button
    bar.select(0)

    root.mainloop()


if __name__ == "__main__":
    main()
