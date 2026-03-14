"""
ED Assist — Main Client Window
================================
Root client window that hosts the ActivityBar and dynamically renders only
the role panels assigned to this client by the agent.

Layout
------
  ┌──────────────────────────────────────────────────────┐
  │  ActivityBar  (role selector, horizontal icon bar)   │
  ├──────────────────────────────────────────────────────┤
  │                                                      │
  │  Active role panel (fills remaining space)           │
  │                                                      │
  └──────────────────────────────────────────────────────┘

Behaviour
---------
  • Subscribes to the EDClient's status queue to receive connection events.
  • On "connected": builds the ActivityBar and panel set for the assigned roles.
  • On "roles_updated": tears down current panels and rebuilds.
  • On "disconnected" / "auth_failed": shows a reconnecting / error message.
  • Clicking an icon in the ActivityBar raises the matching panel.
  • Only panels for assigned roles are ever created.
"""
from __future__ import annotations

import queue
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from client.core.ed_client import EDClient

from client.roles import create_panel, all_panel_role_names
from shared.roles_def import Role

# ── Theme (matching the dark ED palette) ──────────────────────────────────────

BG        = "#0d0d1e"
PANEL_BG  = "#10102a"
HEADER_BG = "#b87800"
HEADER_FG = "#ffff00"
ACCENT    = "#4da6ff"
TEXT_FG   = "#ffffff"
GREY_FG   = "#555577"
FONT_BOLD = ("Consolas", 10, "bold")
FONT_BODY = ("Consolas", 9)
FONT_TINY = ("Consolas", 8, "italic")

# Icon attributes in icons_b64 keyed by canonical role name
_ROLE_ICON_ATTR: dict[str, str] = {
    Role.EXOBIOLOGY:         "EXOBIOLOGY",
    Role.MINING:             "MINING",
    Role.SESSION_MONITORING: "STATS_STATUS",
    Role.NAVIGATION:         "SURFACE_NAV",
}

# Human-readable label for each role
_ROLE_LABEL: dict[str, str] = {
    Role.EXOBIOLOGY:         "Exobiology",
    Role.MINING:             "Mining",
    Role.SESSION_MONITORING: "Session",
    Role.NAVIGATION:         "Navigation",
}

POLL_MS = 200   # status queue drain interval


class MainWindow(tk.Toplevel):
    """
    Main client window.

    Subscribes to EDClient's status queue and rebuilds the panel set
    whenever the assigned roles change.
    """

    def __init__(
        self,
        parent: tk.Misc,
        client: "EDClient",
        quit_on_close: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(parent, **kwargs)
        self.title("ED Assist")
        self.configure(bg=BG)
        self.minsize(700, 400)

        self._client        = client
        self._quit_on_close = quit_on_close

        # Per-role panel instances and their event queues
        self._panels:  dict[str, ttk.Frame] = {}
        self._queues:  dict[str, queue.Queue] = {}
        self._active_role: Optional[str] = None

        # Status queue from EDClient
        self._status_q = client.subscribe_status()

        # Build the skeleton UI (activity bar slot + content area)
        self._build_skeleton()

        # Show "connecting" immediately
        self._show_connecting()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(POLL_MS, self._poll_status)

    # ── Skeleton ────────────────────────────────────────────────────────────

    def _build_skeleton(self) -> None:
        """Create the persistent outer frames; panels go inside _content."""
        # Activity bar area (top strip — will be repopulated per role set)
        self._bar_frame = tk.Frame(self, bg=BG)
        self._bar_frame.pack(fill="x", side="top")

        # Separator
        tk.Frame(self, bg="#1a2a4a", height=2).pack(fill="x", side="top")

        # Content area — only one panel visible at a time
        self._content = tk.Frame(self, bg=BG)
        self._content.pack(fill="both", expand=True, side="top")
        self._content.rowconfigure(0, weight=1)
        self._content.columnconfigure(0, weight=1)

    # ── Status polling ──────────────────────────────────────────────────────

    def _poll_status(self) -> None:
        try:
            while True:
                status = self._status_q.get_nowait()
                self._handle_status(status)
        except queue.Empty:
            pass
        self.after(POLL_MS, self._poll_status)

    def _handle_status(self, status: dict) -> None:
        s = status.get("status", "")
        if s == "connecting":
            self._show_connecting()
        elif s == "connected":
            self._build_panels(status.get("roles", []))
        elif s == "roles_updated":
            self._build_panels(status.get("roles", []))
        elif s == "disconnected":
            self._show_message("Reconnecting to agent…", GREY_FG)
        elif s == "auth_failed":
            msg = status.get("message", "Authentication failed.")
            self._show_message(f"Auth failed: {msg}", "#cc2222")
        elif s == "cert_pinned":
            pass   # silent TOFU — no UI change needed

    # ── Panel lifecycle ─────────────────────────────────────────────────────

    def _build_panels(self, roles: list[str]) -> None:
        """Destroy existing panels and build a fresh set for ``roles``."""
        # Unsubscribe old role queues
        for role, q in self._queues.items():
            self._client.unsubscribe_role(role, q)
        self._queues.clear()

        # Destroy old panel widgets
        for panel in self._panels.values():
            panel.destroy()
        self._panels.clear()
        self._active_role = None

        # Destroy old activity bar widgets
        for w in self._bar_frame.winfo_children():
            w.destroy()

        # Filter to only known/supported roles, preserving order
        supported = [r for r in roles if r in all_panel_role_names()]

        if not supported:
            self._show_message(
                "No supported roles assigned to this client.", GREY_FG)
            return

        # ── Create panels (hidden initially) ──────────────────────────────
        for role in supported:
            q = self._client.subscribe_role(role)
            self._queues[role] = q
            panel = create_panel(
                role, self._content, q,
                action_callback=self._client.send_action,
            )
            panel.grid(row=0, column=0, sticky="nsew")
            panel.grid_remove()   # hide until selected
            self._panels[role] = panel

        # ── Build activity bar ─────────────────────────────────────────────
        self._build_activity_bar(supported)

        # Activate first role
        self._activate_role(supported[0])

    def _build_activity_bar(self, roles: list[str]) -> None:
        """Populate the activity bar with one button per role."""
        try:
            from client.GUI import icons_b64 as _icons
        except ImportError:
            _icons = None

        self._bar_buttons: dict[str, tuple] = {}   # role → (border, btn, lbl)

        container = tk.Frame(self._bar_frame, bg=BG)
        container.pack(padx=8, pady=4)

        for col, role in enumerate(roles):
            icon_attr = _ROLE_ICON_ATTR.get(role)
            img = None
            if _icons and icon_attr:
                try:
                    img = tk.PhotoImage(data=getattr(_icons, icon_attr))
                except Exception:
                    pass

            border = tk.Frame(container, bg="#2a2a4a", padx=2, pady=2)
            border.grid(row=0, column=col, padx=5, pady=4)

            btn_frame = tk.Frame(border, bg="#14142e")
            btn_frame.pack()

            btn_kwargs: dict = dict(
                bg="#14142e",
                activebackground="#0a1428",
                relief="flat", bd=0,
                cursor="hand2",
                command=lambda r=role: self._activate_role(r),
            )
            if img:
                btn_kwargs["image"] = img
                if not hasattr(self, "_img_refs"):
                    self._img_refs: list = []
                self._img_refs.append(img)
            else:
                btn_kwargs["text"] = _ROLE_LABEL.get(role, role)
                btn_kwargs["fg"] = ACCENT
                btn_kwargs["font"] = FONT_BOLD
                btn_kwargs["padx"] = 10
                btn_kwargs["pady"] = 6

            btn = tk.Button(btn_frame, **btn_kwargs)
            btn.pack(padx=1, pady=1)

            lbl = tk.Label(container,
                           text=_ROLE_LABEL.get(role, role),
                           bg=BG, fg=GREY_FG, font=FONT_TINY)
            lbl.grid(row=1, column=col, pady=(0, 2))

            for w in (btn, btn_frame, lbl):
                w.bind("<Enter>",  lambda e, r=role: self._bar_hover(r, True))
                w.bind("<Leave>",  lambda e, r=role: self._bar_hover(r, False))

            self._bar_buttons[role] = (border, btn, lbl)

    def _activate_role(self, role: str) -> None:
        """Switch the visible panel to ``role`` and update the bar highlight."""
        if role not in self._panels:
            return

        # Deselect old
        if self._active_role and self._active_role in self._bar_buttons:
            b, _, lbl = self._bar_buttons[self._active_role]
            b.configure(bg="#2a2a4a")
            lbl.configure(fg=GREY_FG, font=FONT_TINY)
            if self._active_role in self._panels:
                self._panels[self._active_role].grid_remove()

        # Select new
        self._active_role = role
        if role in self._bar_buttons:
            b, _, lbl = self._bar_buttons[role]
            b.configure(bg=ACCENT)
            lbl.configure(fg=ACCENT, font=("Consolas", 8, "bold"))
        self._panels[role].grid()

    def _bar_hover(self, role: str, entering: bool) -> None:
        if role not in self._bar_buttons:
            return
        border, btn, _ = self._bar_buttons[role]
        if role == self._active_role:
            return
        border.configure(bg=ACCENT if entering else "#2a2a4a")
        btn.master.configure(bg="#1e2a4a" if entering else "#14142e")
        btn.configure(bg="#1e2a4a" if entering else "#14142e")

    # ── Utility message display ─────────────────────────────────────────────

    def _show_connecting(self) -> None:
        self._show_message("Connecting to agent…", GREY_FG)

    def _show_message(self, text: str, color: str = TEXT_FG) -> None:
        """Replace content area with a centred status message."""
        for w in self._content.winfo_children():
            w.destroy()
        self._panels.clear()
        self._active_role = None

        for w in self._bar_frame.winfo_children():
            w.destroy()

        tk.Label(
            self._content,
            text=text,
            bg=BG, fg=color,
            font=FONT_BODY,
        ).grid(row=0, column=0)

    # ── Window close ────────────────────────────────────────────────────────

    def _on_close(self) -> None:
        self._client.unsubscribe_status(self._status_q)
        for role, q in self._queues.items():
            self._client.unsubscribe_role(role, q)
        self.destroy()
        if self._quit_on_close:
            self.master.quit()
