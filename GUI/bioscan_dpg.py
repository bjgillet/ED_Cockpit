"""
BioScan Table — Dear PyGui version.
GPU-accelerated equivalent of bioscan_table.py (tkinter version).

Install:  pip install dearpygui
Run:      python3 bioscan_dpg.py
"""
import dearpygui.dearpygui as dpg

# ---------------------------------------------------------------------------
# Theme colours  (R, G, B, A)
# ---------------------------------------------------------------------------
BG_COLOR    = (13,  13,  30,  255)
BG_ALT      = (20,  20,  45,  255)   # alternate row tint
HEADER_BG   = (184, 120, 0,   255)   # amber header background
HEADER_FG   = (255, 255, 0,   255)   # yellow header text
PARENT_FG   = (77,  166, 255, 255)   # blue  – body rows
CHILD_FG    = (255, 255, 255, 255)   # white – species rows
GREY_FG     = (136, 136, 136, 255)   # grey  – unidentified rows
SEL_BG      = (26,  42,  74,  255)   # selection / hover
BORDER      = (60,  60,  90,  255)
BTN_BG      = (30,  30,  55,  255)

# ---------------------------------------------------------------------------
# Sample data  (mirrors the screenshot)
# ---------------------------------------------------------------------------
SAMPLE_DATA = [
    {
        "body":         "Wredgau GQ-G d10-122 1 c (289 ls)",
        "remaining_cr": "0",
        "scanned_cr":   "5,264,500",
        "species": [
            {"name": "Bacterium Aurasus - Teal",   "scanned_cr": "1,000,000", "hist": "4",  "done": "Y", "gc": True},
            {"name": "Tubus Conifer - Teal",        "scanned_cr": "2,415,500", "hist": "3",  "done": "Y", "gc": True},
            {"name": "Tussock Ignis - Emerald",     "scanned_cr": "1,849,000", "hist": "3",  "done": "Y", "gc": True},
        ],
    },
    {
        "body":         "Wredgau GQ-G d10-122 1 d (286 ls)",
        "remaining_cr": "0",
        "scanned_cr":   "5,264,500",
        "species": [
            {"name": "Bacterium Aurasus - Teal",   "scanned_cr": "1,000,000", "hist": "4",  "done": "Y", "gc": True},
            {"name": "Tubus Conifer - Teal",        "scanned_cr": "2,415,500", "hist": "3",  "done": "Y", "gc": True},
            {"name": "Tussock Ignis - Emerald",     "scanned_cr": "1,849,000", "hist": "3",  "done": "Y", "gc": True},
        ],
    },
    {
        "body":         "Wredgau GQ-G d10-122 1 e (295 ls)",
        "remaining_cr": "0",
        "scanned_cr":   "5,264,500",
        "species": [
            {"name": "Tubus Conifer - Teal",        "scanned_cr": "2,415,500", "hist": "3",  "done": "Y", "gc": True},
            {"name": "Tussock Ignis - Emerald",     "scanned_cr": "1,849,000", "hist": "3",  "done": "Y", "gc": True},
            {"name": "Bacterium Aurasus - Teal",    "scanned_cr": "1,000,000", "hist": "4",  "done": "Y", "gc": True},
        ],
    },
    {
        "body":         "Wredgau GQ-G d10-122 6 a (1544 ls)",
        "remaining_cr": "0",
        "scanned_cr":   "2,000,000",
        "species": [
            {"name": "Bacterium Vesicula - Red",          "scanned_cr": "1,000,000", "hist": "7",  "done": "Y", "gc": True},
            {"name": "Fonticulua Campestris - Amethyst",  "scanned_cr": "1,000,000", "hist": "17", "done": "Y", "gc": True},
        ],
    },
    {
        "body":         "Wredgau GQ-G d10-122 6 b (1547 ls)",
        "remaining_cr": "",
        "scanned_cr":   "",
        "species": [
            {"name": "UNIDENTIFIED (needs DSS scan)", "remaining_cr": "?", "scanned_cr": "", "hist": "", "done": "", "gc": False},
            {"name": "UNIDENTIFIED (needs DSS scan)", "remaining_cr": "?", "scanned_cr": "", "hist": "", "done": "", "gc": False},
        ],
    },
    {
        "body":         "Wredgau GQ-G d10-122 6 c (1550 ls)",
        "remaining_cr": "",
        "scanned_cr":   "",
        "species": [
            {"name": "UNIDENTIFIED (needs DSS scan)", "remaining_cr": "?", "scanned_cr": "", "hist": "", "done": "", "gc": False},
            {"name": "UNIDENTIFIED (needs DSS scan)", "remaining_cr": "?", "scanned_cr": "", "hist": "", "done": "", "gc": False},
        ],
    },
    {
        "body":         "Wredgau GQ-G d10-122 6 d (1552 ls)",
        "remaining_cr": "",
        "scanned_cr":   "",
        "species": [
            {"name": "UNIDENTIFIED (needs DSS scan)", "remaining_cr": "?", "scanned_cr": "", "hist": "", "done": "", "gc": False},
            {"name": "UNIDENTIFIED (needs DSS scan)", "remaining_cr": "?", "scanned_cr": "", "hist": "", "done": "", "gc": False},
        ],
    },
]

# ---------------------------------------------------------------------------
# Application class
# ---------------------------------------------------------------------------
class BioScanApp:
    """
    Manages the Dear PyGui BioScan table.
    Each body is a collapsible parent row; species are child rows.
    """

    COLS = [
        # (label,          width)
        ("BODY",           288),
        ("SPECIES",        240),
        ("REMAINING CR",   115),
        ("SCANNED CR",     115),
        ("HIST",            50),
        ("DONE",            50),
        ("GC",              40),
    ]

    def __init__(self, data: list):
        self.data           = data
        self._expanded: dict[int, bool]        = {}
        self._child_rows:  dict[int, list[int]] = {}
        self._arrow_btns:  dict[int, int]       = {}
        self._row_counter  = 0  # unique tag counter

    # ------------------------------------------------------------------
    def _next_tag(self) -> str:
        self._row_counter += 1
        return f"__row_{self._row_counter}"

    # ------------------------------------------------------------------
    def _toggle_callback(self, sender, app_data, user_data: int):
        idx = user_data
        self._expanded[idx] = not self._expanded[idx]
        show = self._expanded[idx]
        dpg.set_item_label(self._arrow_btns[idx], "v" if show else ">")
        for row_tag in self._child_rows[idx]:
            dpg.configure_item(row_tag, show=show)

    # ------------------------------------------------------------------
    def _apply_global_theme(self):
        with dpg.theme() as theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg,        BG_COLOR)
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg,         BG_COLOR)
                dpg.add_theme_color(dpg.mvThemeCol_PopupBg,         BG_COLOR)
                dpg.add_theme_color(dpg.mvThemeCol_Text,            CHILD_FG)
                dpg.add_theme_color(dpg.mvThemeCol_Border,          BORDER)
                # Table
                dpg.add_theme_color(dpg.mvThemeCol_TableHeaderBg,    HEADER_BG)
                dpg.add_theme_color(dpg.mvThemeCol_TableBorderLight,  BORDER)
                dpg.add_theme_color(dpg.mvThemeCol_TableBorderStrong, BORDER)
                dpg.add_theme_color(dpg.mvThemeCol_TableRowBg,        BG_COLOR)
                dpg.add_theme_color(dpg.mvThemeCol_TableRowBgAlt,     BG_ALT)
                # Header hover/active — keep amber colour
                dpg.add_theme_color(dpg.mvThemeCol_Header,           HEADER_BG)
                dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered,    HEADER_BG)
                dpg.add_theme_color(dpg.mvThemeCol_HeaderActive,     HEADER_BG)
                # Buttons (collapse arrows)
                dpg.add_theme_color(dpg.mvThemeCol_Button,           BTN_BG)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,    SEL_BG)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,     SEL_BG)
                # Scrollbar
                dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg,          BG_COLOR)
                dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab,         (80, 80, 120, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrabHovered,  (100, 100, 160, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrabActive,   PARENT_FG)
                # Separator
                dpg.add_theme_color(dpg.mvThemeCol_Separator, BORDER)
        dpg.bind_theme(theme)

    # ------------------------------------------------------------------
    def _build_table(self):
        with dpg.table(
            tag="bioscan_table",
            header_row=True,
            borders_innerH=True,
            borders_outerH=True,
            borders_innerV=True,
            borders_outerV=True,
            row_background=True,
            resizable=True,
            scrollY=True,
            scrollX=True,
            freeze_rows=1,       # keep header visible while scrolling
        ):
            for label, width in self.COLS:
                dpg.add_table_column(
                    label=label,
                    width_fixed=True,
                    init_width_or_weight=width,
                )

            for idx, body in enumerate(self.data):
                self._expanded[idx]   = True
                self._child_rows[idx] = []

                # ── Parent row ──────────────────────────────────────────
                with dpg.table_row():
                    # Col 0 — BODY: collapse button + body name
                    with dpg.group(horizontal=True):
                        btn = dpg.add_button(
                            label="v",
                            width=18, height=16,
                            callback=self._toggle_callback,
                            user_data=idx,
                        )
                        self._arrow_btns[idx] = btn
                        dpg.add_text(body["body"], color=PARENT_FG)

                    # Col 1 — SPECIES: empty for parent
                    dpg.add_text("")

                    # Col 2 — REMAINING CR
                    dpg.add_text(body.get("remaining_cr", ""), color=PARENT_FG)

                    # Col 3 — SCANNED CR
                    dpg.add_text(body.get("scanned_cr", ""), color=PARENT_FG)

                    # Cols 4-6 — HIST / DONE / GC: n/a for parent
                    dpg.add_text("")
                    dpg.add_text("")
                    dpg.add_text("")

                # ── Child rows ──────────────────────────────────────────
                for sp in body.get("species", []):
                    unidentified = "UNIDENTIFIED" in sp["name"]
                    col      = GREY_FG if unidentified else CHILD_FG
                    gc_label = "■" if sp.get("gc") else ""
                    row_tag  = self._next_tag()

                    with dpg.table_row(tag=row_tag):
                        dpg.add_text("")                                       # BODY
                        dpg.add_text("    " + sp["name"], color=col)           # SPECIES (indent)
                        dpg.add_text(sp.get("remaining_cr", ""), color=col)   # REMAINING CR
                        dpg.add_text(sp.get("scanned_cr",   ""), color=col)   # SCANNED CR
                        dpg.add_text(sp.get("hist",         ""), color=col)   # HIST
                        dpg.add_text(sp.get("done",         ""), color=col)   # DONE
                        dpg.add_text(gc_label,                   color=col)   # GC

                    self._child_rows[idx].append(row_tag)

    # ------------------------------------------------------------------
    def run(self):
        dpg.create_context()
        dpg.create_viewport(
            title="BioScan — Exobiological Survey",
            width=1000,
            height=540,
            clear_color=BG_COLOR,
        )

        self._apply_global_theme()

        with dpg.window(
            label="Exobiological Survey",
            tag="main_window",
            no_close=True,
            no_move=True,
            no_collapse=True,
        ):
            with dpg.group(horizontal=True):
                dpg.add_text("EXOBIOLOGICAL SURVEY", color=HEADER_FG)
                dpg.add_text("  —  click [ v / > ] to expand / collapse a body",
                             color=GREY_FG)
            dpg.add_spacer(height=4)
            self._build_table()

        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window("main_window", True)

        # ── Main loop ─────────────────────────────────────────────────
        while dpg.is_dearpygui_running():
            # Resize table to fill window (minus top bar ~55 px)
            w, h = dpg.get_viewport_width(), dpg.get_viewport_height()
            dpg.set_item_width("bioscan_table",  w - 16)
            dpg.set_item_height("bioscan_table", h - 55)
            dpg.render_dearpygui_frame()

        dpg.destroy_context()


# ---------------------------------------------------------------------------
def main():
    app = BioScanApp(SAMPLE_DATA)
    app.run()


if __name__ == "__main__":
    main()
