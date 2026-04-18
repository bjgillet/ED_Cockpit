from __future__ import annotations

import tkinter as tk


class ScrollablePanelContainer(tk.Frame):
    """
    Canvas-backed panel container with auto-show scrollbars.

    - Vertical and horizontal scrollbars appear only on overflow.
    - Supports wheel (vertical) and shift+wheel (horizontal).
    """

    def __init__(self, parent: tk.Misc, bg: str) -> None:
        super().__init__(parent, bg=bg)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")

        self.v_scroll = tk.Scrollbar(self, orient="vertical", command=self._on_y_scroll)
        self.h_scroll = tk.Scrollbar(self, orient="horizontal", command=self._on_x_scroll)
        self.canvas.configure(
            xscrollcommand=self._on_xview_changed,
            yscrollcommand=self._on_yview_changed,
        )

        self.body = tk.Frame(self.canvas, bg=bg)
        self._canvas_window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.body.bind("<Configure>", self._on_body_configure)

    def bind_mousewheel_targets(self, *widgets: tk.Misc) -> None:
        targets = widgets if widgets else (self, self.canvas, self.body)
        for widget in targets:
            widget.bind("<MouseWheel>", self._on_mousewheel, add="+")
            widget.bind("<Shift-MouseWheel>", self._on_shift_mousewheel, add="+")
            widget.bind("<Button-4>", self._on_linux_wheel_up, add="+")
            widget.bind("<Button-5>", self._on_linux_wheel_down, add="+")
            widget.bind("<Shift-Button-4>", self._on_linux_shift_wheel_up, add="+")
            widget.bind("<Shift-Button-5>", self._on_linux_shift_wheel_down, add="+")

    def refresh_layout(self) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._update_scrollbars()

    def _can_scroll_vertically(self) -> bool:
        return self.v_scroll.winfo_ismapped()

    def _can_scroll_horizontally(self) -> bool:
        return self.h_scroll.winfo_ismapped()

    @staticmethod
    def _wheel_units_from_delta(delta: int) -> int:
        if delta == 0:
            return 0
        if abs(delta) >= 120:
            return int(delta / 120)
        return 1 if delta > 0 else -1

    def _scroll_x_units(self, units: int) -> None:
        if units == 0 or not self._can_scroll_horizontally():
            return
        self.canvas.xview_scroll(-units, "units")
        self._update_scrollbars()

    def _scroll_y_units(self, units: int) -> None:
        if units == 0 or not self._can_scroll_vertically():
            return
        self.canvas.yview_scroll(-units, "units")
        self._update_scrollbars()

    def _on_mousewheel(self, event: tk.Event) -> str:
        self._scroll_y_units(self._wheel_units_from_delta(getattr(event, "delta", 0)))
        return "break"

    def _on_shift_mousewheel(self, event: tk.Event) -> str:
        self._scroll_x_units(self._wheel_units_from_delta(getattr(event, "delta", 0)))
        return "break"

    def _on_linux_wheel_up(self, _event: tk.Event) -> str:
        self._scroll_y_units(1)
        return "break"

    def _on_linux_wheel_down(self, _event: tk.Event) -> str:
        self._scroll_y_units(-1)
        return "break"

    def _on_linux_shift_wheel_up(self, _event: tk.Event) -> str:
        self._scroll_x_units(1)
        return "break"

    def _on_linux_shift_wheel_down(self, _event: tk.Event) -> str:
        self._scroll_x_units(-1)
        return "break"

    def _on_x_scroll(self, *args) -> None:
        self.canvas.xview(*args)
        self._update_scrollbars()

    def _on_y_scroll(self, *args) -> None:
        self.canvas.yview(*args)
        self._update_scrollbars()

    def _on_xview_changed(self, first: str, last: str) -> None:
        self.h_scroll.set(first, last)
        self._update_scrollbars()

    def _on_yview_changed(self, first: str, last: str) -> None:
        self.v_scroll.set(first, last)
        self._update_scrollbars()

    def _on_body_configure(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._update_scrollbars()

    def _on_canvas_configure(self, _event=None) -> None:
        self._update_scrollbars()

    def _update_scrollbars(self) -> None:
        bbox = self.canvas.bbox("all")
        if not bbox:
            self.v_scroll.grid_remove()
            self.h_scroll.grid_remove()
            return

        content_w = bbox[2] - bbox[0]
        content_h = bbox[3] - bbox[1]
        viewport_w = self.canvas.winfo_width()
        viewport_h = self.canvas.winfo_height()

        show_h = content_w > viewport_w
        show_v = content_h > viewport_h

        if show_v:
            self.v_scroll.grid(row=0, column=1, sticky="ns")
        else:
            self.v_scroll.grid_remove()
            self.canvas.yview_moveto(0)

        if show_h:
            self.h_scroll.grid(row=1, column=0, sticky="ew")
        else:
            self.h_scroll.grid_remove()
            self.canvas.xview_moveto(0)
