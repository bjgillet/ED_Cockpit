"""
BioScan Table — tkinter/ttk hierarchical table.
3-level layout: SYSTEM (root) → BODY (parent) → SPECIES (child).

Columns: SPECIES | REMAINING CR | SCANNED CR | HIST | DONE | GC
"""
import tkinter as tk
from tkinter import ttk

# ---------------------------------------------------------------------------
# Theme colours
# ---------------------------------------------------------------------------
BG          = "#0d0d1e"   # very dark navy background
HEADER_BG   = "#b87800"   # amber header background
HEADER_FG   = "#ffff00"   # yellow header text
SYSTEM_FG   = "#ffd966"   # gold  – system (root) rows
PARENT_FG   = "#4da6ff"   # blue  – body (parent) rows
CHILD_FG    = "#ffffff"   # white – species (child) rows
GREY_FG     = "#888888"   # grey  – unidentified rows
SEL_BG      = "#1a2a4a"   # selection highlight
FONT        = ("Consolas", 10)
FONT_BOLD   = ("Consolas", 10, "bold")

# ---------------------------------------------------------------------------
# Sample data  (grouped by system)
# ---------------------------------------------------------------------------
SAMPLE_DATA = [
    {
        "system":       "Wredgau GQ-G d10-122",
        "remaining_cr": "0",
        "scanned_cr":   "22,058,000",
        "bodies": [
            {
                "body":         "1 c (289 ls)",
                "remaining_cr": "0",
                "scanned_cr":   "5,264,500",
                "species": [
                    {"name": "Bacterium Aurasus - Teal",   "scanned_cr": "1,000,000", "hist": "4",  "done": "Y", "gc": True},
                    {"name": "Tubus Conifer - Teal",        "scanned_cr": "2,415,500", "hist": "3",  "done": "Y", "gc": True},
                    {"name": "Tussock Ignis - Emerald",     "scanned_cr": "1,849,000", "hist": "3",  "done": "Y", "gc": True},
                ],
            },
            {
                "body":         "1 d (286 ls)",
                "remaining_cr": "0",
                "scanned_cr":   "5,264,500",
                "species": [
                    {"name": "Bacterium Aurasus - Teal",   "scanned_cr": "1,000,000", "hist": "4",  "done": "Y", "gc": True},
                    {"name": "Tubus Conifer - Teal",        "scanned_cr": "2,415,500", "hist": "3",  "done": "Y", "gc": True},
                    {"name": "Tussock Ignis - Emerald",     "scanned_cr": "1,849,000", "hist": "3",  "done": "Y", "gc": True},
                ],
            },
            {
                "body":         "6 b (1547 ls)",
                "remaining_cr": "",
                "scanned_cr":   "",
                "species": [
                    {"name": "UNIDENTIFIED (needs DSS scan)", "remaining_cr": "?", "scanned_cr": "", "hist": "", "done": "", "gc": False},
                    {"name": "UNIDENTIFIED (needs DSS scan)", "remaining_cr": "?", "scanned_cr": "", "hist": "", "done": "", "gc": False},
                ],
            },
        ],
    },
    {
        "system":       "Flyooe Hypue AA-A h0",
        "remaining_cr": "1,849,000",
        "scanned_cr":   "1,849,000",
        "bodies": [
            {
                "body":         "AB 3 a",
                "remaining_cr": "1,849,000",
                "scanned_cr":   "1,849,000",
                "ff":           True,
                "species": [
                    {"name": "Tussock Pennula - Lime",   "remaining_cr": "",          "scanned_cr": "1,849,000", "hist": "3", "done": "Y", "gc": False},
                    {"name": "Tussock Propagito - Lime", "remaining_cr": "1,849,000", "scanned_cr": "",          "hist": "2", "done": "",  "gc": False},
                ],
            },
        ],
    },
]


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------
class BioScanTable(tk.Frame):
    """
    Hierarchical table with three levels:
      • System (root row)  — gold, bold
      • Body   (child of system) — blue
      • Species (child of body)  — white / grey
    """

    COLUMNS = (
        ("species",      "SPECIES",      240, "w"),
        ("remaining_cr", "REMAINING CR", 115, "center"),
        ("scanned_cr",   "SCANNED CR",   115, "center"),
        ("hist",         "HIST",          50, "center"),
        ("done",         "DONE",          50, "center"),
        ("gc",           "GC",            40, "center"),
    )

    def __init__(self, parent, data: list | None = None, **kwargs):
        super().__init__(parent, bg=BG, **kwargs)
        self.data = data or []
        self._setup_style()
        self._build_tree()
        self._populate()

    # ------------------------------------------------------------------
    def _setup_style(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")

        style.configure(
            "BioScan.Treeview",
            background=BG,
            foreground=CHILD_FG,
            fieldbackground=BG,
            borderwidth=0,
            rowheight=22,
            font=FONT,
        )
        style.configure(
            "BioScan.Treeview.Heading",
            background=HEADER_BG,
            foreground=HEADER_FG,
            relief="flat",
            font=FONT_BOLD,
        )
        style.map(
            "BioScan.Treeview",
            background=[("selected", SEL_BG)],
            foreground=[("selected", PARENT_FG)],
        )
        style.map(
            "BioScan.Treeview.Heading",
            background=[("active", HEADER_BG)],
            foreground=[("active", HEADER_FG)],
        )

    # ------------------------------------------------------------------
    def _build_tree(self) -> None:
        col_ids = tuple(c[0] for c in self.COLUMNS)

        self.tree = ttk.Treeview(
            self,
            columns=col_ids,
            style="BioScan.Treeview",
            show="tree headings",
            selectmode="browse",
        )

        # Column #0 — SYSTEM / BODY (tree hierarchy column)
        self.tree.heading("#0", text="SYSTEM / BODY", anchor="w")
        self.tree.column("#0", width=270, minwidth=150, anchor="w", stretch=False)

        for col_id, heading, width, anchor in self.COLUMNS:
            self.tree.heading(col_id, text=heading, anchor="center")
            self.tree.column(col_id, width=width, minwidth=30, anchor=anchor, stretch=False)

        # Row tags
        self.tree.tag_configure("system_row",   foreground=SYSTEM_FG, background=BG, font=FONT_BOLD)
        self.tree.tag_configure("parent",       foreground=PARENT_FG, background=BG)
        self.tree.tag_configure("child",        foreground=CHILD_FG,  background=BG)
        self.tree.tag_configure("unidentified", foreground=GREY_FG,   background=BG)

        # Scrollbars
        vsb = ttk.Scrollbar(self, orient="vertical",   command=self.tree.yview)
        hsb = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

    # ------------------------------------------------------------------
    def _populate(self) -> None:
        for system in self.data:
            sys_id = self.tree.insert(
                "", "end",
                text=system["system"],
                values=(
                    "",
                    system.get("remaining_cr", ""),
                    system.get("scanned_cr", ""),
                    "", "", "",
                ),
                tags=("system_row",),
                open=True,
            )

            for body in system.get("bodies", []):
                ff_mark  = " ⭐" if body.get("ff") else ""
                body_id = self.tree.insert(
                    sys_id, "end",
                    text=body["body"] + ff_mark,
                    values=(
                        "",
                        body.get("remaining_cr", ""),
                        body.get("scanned_cr", ""),
                        "", "", "",
                    ),
                    tags=("parent",),
                    open=True,
                )

                for sp in body.get("species", []):
                    unidentified = "UNIDENTIFIED" in sp["name"]
                    tag = "unidentified" if unidentified else "child"
                    gc_symbol = "⬛" if sp.get("gc") else ""

                    self.tree.insert(
                        body_id, "end",
                        text="",
                        values=(
                            sp["name"],
                            sp.get("remaining_cr", ""),
                            sp.get("scanned_cr", ""),
                            sp.get("hist", ""),
                            sp.get("done", ""),
                            gc_symbol,
                        ),
                        tags=(tag,),
                    )

    # ------------------------------------------------------------------
    def load_data(self, data: list) -> None:
        """Replace table content with new data."""
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.data = data
        self._populate()


# ---------------------------------------------------------------------------
# Main (standalone demo)
# ---------------------------------------------------------------------------
def main() -> None:
    root = tk.Tk()
    root.title("BioScan — Exobiological Survey")
    root.configure(bg=BG)
    root.geometry("960x480")
    root.minsize(640, 300)

    table = BioScanTable(root, data=SAMPLE_DATA)
    table.pack(fill="both", expand=True, padx=4, pady=4)

    root.mainloop()


if __name__ == "__main__":
    main()
