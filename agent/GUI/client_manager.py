"""
ED Cockpit — Client Manager Window
====================================
Agent-side Tkinter window that lets the operator manage all registered
clients: view online status, edit roles, rename, add new clients, and
revoke access.

Layout
------
  ┌──────────────────────────────────────────────────────────────┐
  │  ED COCKPIT — CLIENT MANAGER                                  │
  ├──────────────────────────────────────────────────────────────┤
  │  Label / ID        Last Seen      Status    Roles            │
  │  ─────────────────────────────────────────────────────────── │
  │  Tablet – Exobio   2 min ago      ● Online  exobiology       │
  │  ed-client-2b9c    just now       ● Online  mining           │
  │  ed-client-a1d0    never          ○ Offline —                │
  ├──────────────────────────────────────────────────────────────┤
  │  [Add Client]  [Edit Roles]  [Rename]  [Copy Token]  [Revoke]│
  └──────────────────────────────────────────────────────────────┘

Refresh
-------
  The table auto-refreshes every POLL_MS ms.  Online/offline state is
  derived by comparing the registry records against EDApp.connected_client_ids().

Dialogs
-------
  AddClientDialog  — generates a new Client_ID + token, shows them once.
  RoleEditor       — checkboxes for every defined role.
  RenameDialog     — single entry field to set the client label.
"""
from __future__ import annotations

import queue
import tkinter as tk
from tkinter import ttk, messagebox
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.core.ed_app import EDApp

from agent.network.client_registry import ClientRecord
from agent.security.tokens import generate_token, hash_token
from shared.roles_def import ALL_ROLES, Role

# ── Theme ─────────────────────────────────────────────────────────────────────

BG        = "#0d0d1e"
PANEL_BG  = "#10102a"
HEADER_BG = "#b87800"
HEADER_FG = "#ffff00"
ACCENT    = "#4da6ff"
TEXT_FG   = "#ffffff"
GREEN_FG  = "#00cc55"
GREY_FG   = "#555577"
RED_FG    = "#cc2222"
SEP_COLOR = "#2a2a4a"

FONT_TITLE = ("Consolas", 13, "bold")
FONT_HEAD  = ("Consolas", 10, "bold")
FONT_BODY  = ("Consolas",  9)
FONT_TINY  = ("Consolas",  8)
FONT_BTN   = ("Consolas",  9, "bold")

POLL_MS      = 2000   # table refresh interval
ACTION_MS    = 200    # action log queue drain interval
MAX_ACTION_LOG = 6    # entries kept in the action log

# Human-readable role labels for the table
_ROLE_ABBREV: dict[str, str] = {
    Role.EXOBIOLOGY:         "Exobio",
    Role.MINING:             "Mining",
    Role.SESSION_MONITORING: "Session",
    Role.NAVIGATION:         "Nav",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_last_seen(ts: str) -> str:
    """Convert an ISO-8601 timestamp to a relative 'N min ago' string."""
    if not ts:
        return "never"
    try:
        from datetime import datetime, timezone
        t = datetime.fromisoformat(ts)
        now = datetime.now(timezone.utc)
        delta = int((now - t).total_seconds())
        if delta < 5:
            return "just now"
        if delta < 60:
            return f"{delta}s ago"
        if delta < 3600:
            return f"{delta // 60}m ago"
        if delta < 86400:
            return f"{delta // 3600}h ago"
        return f"{delta // 86400}d ago"
    except Exception:
        return ts[:16] if len(ts) >= 16 else ts


def _fmt_roles(roles: list[str]) -> str:
    if not roles:
        return "—"
    return ", ".join(_ROLE_ABBREV.get(r, r) for r in roles)


# ── Main window ───────────────────────────────────────────────────────────────

class ClientManager(tk.Toplevel):
    """
    Agent-side client management window.

    Parameters
    ----------
    parent : tk.Misc
        Tkinter parent (usually the hidden root Tk()).
    app : EDApp
        The running agent core — used to read the registry, push role
        updates, add/revoke clients, and query online status.
    """

    def __init__(self, parent: tk.Misc, app: "EDApp", **kwargs) -> None:
        super().__init__(parent, **kwargs)
        self.title("ED Cockpit — Client Manager")
        self.configure(bg=BG)
        self.minsize(680, 320)
        self.resizable(True, True)

        self._app = app
        self._action_queue: queue.Queue = queue.Queue()
        self._action_log: list[str] = []

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._refresh()
        self.after(POLL_MS, self._auto_refresh)
        self.after(ACTION_MS, self._drain_action_queue)

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._build_header()
        self._build_table()
        self._build_button_bar()
        self._build_action_log()

    def _build_header(self) -> None:
        bar = tk.Frame(self, bg=HEADER_BG, pady=6)
        bar.pack(fill="x")
        tk.Label(
            bar, text="ED COCKPIT — CLIENT MANAGER",
            bg=HEADER_BG, fg=HEADER_FG, font=FONT_TITLE,
        ).pack(padx=14)

    def _build_table(self) -> None:
        container = tk.Frame(self, bg=BG)
        container.pack(fill="both", expand=True, padx=8, pady=(6, 2))

        cols = ("status", "label", "last_seen", "roles", "client_id")
        self._tree = ttk.Treeview(
            container,
            columns=cols,
            show="headings",
            selectmode="browse",
        )

        # ── Style ─────────────────────────────────────────────────────────
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Treeview",
                        background=PANEL_BG,
                        foreground=TEXT_FG,
                        rowheight=22,
                        fieldbackground=PANEL_BG,
                        font=FONT_BODY)
        style.configure("Treeview.Heading",
                        background=SEP_COLOR,
                        foreground=HEADER_FG,
                        font=FONT_HEAD)
        style.map("Treeview",
                  background=[("selected", "#1a2a4a")],
                  foreground=[("selected", ACCENT)])

        # ── Columns ───────────────────────────────────────────────────────
        self._tree.heading("status",    text="")
        self._tree.heading("label",     text="Client")
        self._tree.heading("last_seen", text="Last seen")
        self._tree.heading("roles",     text="Roles")
        self._tree.heading("client_id", text="Client ID")

        self._tree.column("status",    width=20,  stretch=False, anchor="center")
        self._tree.column("label",     width=160, stretch=True,  anchor="w")
        self._tree.column("last_seen", width=90,  stretch=False, anchor="w")
        self._tree.column("roles",     width=200, stretch=True,  anchor="w")
        self._tree.column("client_id", width=140, stretch=False, anchor="w")

        vsb = ttk.Scrollbar(container, orient="vertical",
                            command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self._tree.bind("<<TreeviewSelect>>", self._on_select)
        self._tree.bind("<Double-1>", lambda e: self._on_edit_roles())

    def _build_action_log(self) -> None:
        """Build the collapsible action log strip at the bottom."""
        tk.Frame(self, bg=SEP_COLOR, height=1).pack(fill="x")
        hdr = tk.Frame(self, bg="#0d0d1e", pady=2)
        hdr.pack(fill="x")
        tk.Label(hdr, text="  ACTION LOG", bg="#0d0d1e", fg=GREY_FG,
                 font=FONT_TINY, anchor="w").pack(side="left", padx=4)

        log_frame = tk.Frame(self, bg=PANEL_BG)
        log_frame.pack(fill="x", padx=8, pady=(0, 6))

        self._action_listbox = tk.Listbox(
            log_frame,
            bg=PANEL_BG, fg=ACCENT,
            selectbackground=PANEL_BG,
            font=FONT_TINY,
            relief="flat",
            highlightthickness=0,
            activestyle="none",
            height=MAX_ACTION_LOG,
        )
        self._action_listbox.pack(fill="x")

    # ── Public API: called from EDApp (any thread) ─────────────────────────

    def push_action(self, client_id: str, action: str, key: str) -> None:
        """
        Record an action received from a client.

        Thread-safe — may be called from the asyncio loop thread.
        """
        self._action_queue.put_nowait((client_id, action, key))

    # ── Action log drain (tkinter thread) ─────────────────────────────────

    def _drain_action_queue(self) -> None:
        if not self.winfo_exists():
            return
        try:
            while True:
                client_id, action, key = self._action_queue.get_nowait()
                from datetime import datetime
                ts = datetime.now().strftime("%H:%M:%S")
                label = client_id
                rec = self._app.registry.get(client_id)
                if rec and rec.label:
                    label = rec.label
                entry = f"  {ts}  {label:<18}  {action}({key})"
                self._action_log.insert(0, entry)
                if len(self._action_log) > MAX_ACTION_LOG:
                    self._action_log = self._action_log[:MAX_ACTION_LOG]
                self._action_listbox.delete(0, "end")
                for e in self._action_log:
                    self._action_listbox.insert("end", e)
        except queue.Empty:
            pass
        self.after(ACTION_MS, self._drain_action_queue)

    def _build_button_bar(self) -> None:
        bar = tk.Frame(self, bg=BG, pady=6)
        bar.pack(fill="x", padx=8, pady=(0, 0))

        btn_cfg = dict(
            bg="#1a2a4a", fg=ACCENT,
            activebackground="#0a1428", activeforeground=TEXT_FG,
            relief="flat", font=FONT_BTN, cursor="hand2",
            padx=10, pady=4,
        )

        self._btn_add = tk.Button(bar, text="+ Add Client",
                                  command=self._on_add_client, **btn_cfg)
        self._btn_add.pack(side="left", padx=(0, 4))

        self._btn_roles = tk.Button(bar, text="✎ Edit Roles",
                                    command=self._on_edit_roles, **btn_cfg)
        self._btn_roles.pack(side="left", padx=4)
        self._btn_roles.config(state="disabled")

        self._btn_rename = tk.Button(bar, text="✎ Rename",
                                     command=self._on_rename, **btn_cfg)
        self._btn_rename.pack(side="left", padx=4)
        self._btn_rename.config(state="disabled")

        self._btn_copy = tk.Button(bar, text="⎘ Copy Token",
                                   command=self._on_copy_token, **btn_cfg)
        self._btn_copy.pack(side="left", padx=4)
        self._btn_copy.config(state="disabled")

        self._btn_revoke = tk.Button(
            bar, text="✕ Revoke",
            command=self._on_revoke,
            bg="#2a1a1a", fg="#cc6666",
            activebackground="#1a0a0a", activeforeground="#ff8888",
            relief="flat", font=FONT_BTN, cursor="hand2",
            padx=10, pady=4,
        )
        self._btn_revoke.pack(side="right", padx=(4, 0))
        self._btn_revoke.config(state="disabled")

    # ── Table refresh ─────────────────────────────────────────────────────

    def _auto_refresh(self) -> None:
        if not self.winfo_exists():
            return
        self._refresh()
        self.after(POLL_MS, self._auto_refresh)

    def _refresh(self) -> None:
        """Repopulate the Treeview from the registry + online status."""
        online = set(self._app.connected_client_ids())
        selected = self._selected_client_id()

        self._tree.delete(*self._tree.get_children())

        records = sorted(
            self._app.registry.all_records(),
            key=lambda r: (r.client_id not in online, r.client_id),
        )

        for rec in records:
            is_online = rec.client_id in online
            dot   = "●" if is_online else "○"
            label = rec.label or rec.client_id
            tags  = ("online",) if is_online else ("offline",)
            self._tree.insert(
                "", "end",
                iid=rec.client_id,
                values=(dot, label, _fmt_last_seen(rec.last_seen),
                        _fmt_roles(rec.roles), rec.client_id),
                tags=tags,
            )

        self._tree.tag_configure("online",  foreground=GREEN_FG)
        self._tree.tag_configure("offline", foreground=GREY_FG)

        # Restore selection
        if selected and self._tree.exists(selected):
            self._tree.selection_set(selected)
            self._tree.focus(selected)

        self._update_buttons()

    # ── Selection helpers ─────────────────────────────────────────────────

    def _selected_client_id(self) -> str | None:
        sel = self._tree.selection()
        return sel[0] if sel else None

    def _selected_record(self) -> ClientRecord | None:
        cid = self._selected_client_id()
        return self._app.registry.get(cid) if cid else None

    def _on_select(self, _event=None) -> None:
        self._update_buttons()

    def _update_buttons(self) -> None:
        has = bool(self._selected_client_id())
        state = "normal" if has else "disabled"
        for btn in (self._btn_roles, self._btn_rename,
                    self._btn_copy, self._btn_revoke):
            btn.config(state=state)

    # ── Button handlers ───────────────────────────────────────────────────

    def _on_add_client(self) -> None:
        dlg = AddClientDialog(self, self._app)
        self.wait_window(dlg)
        self._refresh()

    def _on_edit_roles(self) -> None:
        rec = self._selected_record()
        if rec is None:
            return
        dlg = RoleEditor(self, self._app, rec)
        self.wait_window(dlg)
        self._refresh()

    def _on_rename(self) -> None:
        rec = self._selected_record()
        if rec is None:
            return
        dlg = RenameDialog(self, self._app, rec)
        self.wait_window(dlg)
        self._refresh()

    def _on_copy_token(self) -> None:
        """
        Copy the stored token hash to the clipboard with a note.

        The raw token is only known at creation time.  This copies the hash
        so the operator can identify the client record in clients.json.
        """
        rec = self._selected_record()
        if rec is None:
            return
        self.clipboard_clear()
        self.clipboard_append(rec.token_hash)
        messagebox.showinfo(
            "Token Hash Copied",
            f"The SHA-256 token hash for\n  {rec.client_id}\nhas been copied to the clipboard.\n\n"
            "Note: the raw token is only displayed once at creation time.\n"
            "To re-issue a token, revoke and re-add the client.",
            parent=self,
        )

    def _on_revoke(self) -> None:
        cid = self._selected_client_id()
        if cid is None:
            return
        rec = self._app.registry.get(cid)
        label = (rec.label or cid) if rec else cid
        if not messagebox.askyesno(
            "Revoke Client",
            f"Remove client  \"{label}\"?\n\n"
            "This will delete the record and disconnect the client "
            "if it is currently online.  The action cannot be undone.",
            icon="warning",
            parent=self,
        ):
            return
        self._app.revoke_client(cid)
        self._refresh()


# ── AddClientDialog ───────────────────────────────────────────────────────────

class AddClientDialog(tk.Toplevel):
    """
    Generate a new Client_ID + token pair, choose initial roles, and
    add the record to the registry.

    The raw token is displayed once and never stored.
    """

    def __init__(self, parent: tk.Misc, app: "EDApp") -> None:
        super().__init__(parent)
        self.title("Add Client")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()

        self._app   = app
        self._token = generate_token()
        self._cid   = _make_client_id()

        self._build_ui()
        self._center_on(parent)

    def _build_ui(self) -> None:
        # Header bar
        hdr = tk.Frame(self, bg=HEADER_BG, pady=4)
        hdr.pack(fill="x")
        tk.Label(hdr, text="ADD NEW CLIENT", bg=HEADER_BG, fg=HEADER_FG,
                 font=FONT_HEAD).pack()

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=8)

        # Label field
        tk.Label(body, text="Label (optional):", bg=BG, fg=ACCENT,
                 font=FONT_BODY).grid(row=0, column=0, sticky="w", pady=4)
        self._label_var = tk.StringVar()
        tk.Entry(body, textvariable=self._label_var,
                 bg=PANEL_BG, fg=TEXT_FG, insertbackground=TEXT_FG,
                 relief="flat", font=FONT_BODY, width=28
                 ).grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=4)

        # Generated Client ID (read-only)
        tk.Label(body, text="Client ID:", bg=BG, fg=ACCENT,
                 font=FONT_BODY).grid(row=1, column=0, sticky="w", pady=4)
        cid_frame = tk.Frame(body, bg=BG)
        cid_frame.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=4)
        self._cid_var = tk.StringVar(value=self._cid)
        cid_entry = tk.Entry(cid_frame, textvariable=self._cid_var,
                             bg=PANEL_BG, fg=ACCENT, relief="flat",
                             font=FONT_BODY, width=22)
        cid_entry.pack(side="left")
        tk.Button(cid_frame, text="⟳", bg=PANEL_BG, fg=ACCENT, relief="flat",
                  font=FONT_BODY, cursor="hand2",
                  command=self._regen_id).pack(side="left", padx=(4, 0))

        # Role checkboxes
        tk.Label(body, text="Roles:", bg=BG, fg=ACCENT,
                 font=FONT_BODY).grid(row=2, column=0, sticky="nw", pady=6)
        role_frame = tk.Frame(body, bg=BG)
        role_frame.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=6)
        self._role_vars: dict[str, tk.BooleanVar] = {}
        for i, role in enumerate(ALL_ROLES):
            var = tk.BooleanVar(value=False)
            self._role_vars[role] = var
            tk.Checkbutton(
                role_frame, text=_ROLE_ABBREV.get(role, role),
                variable=var, bg=BG, fg=TEXT_FG,
                activebackground=BG, activeforeground=ACCENT,
                selectcolor=PANEL_BG, font=FONT_BODY,
            ).grid(row=i // 2, column=i % 2, sticky="w", padx=4)

        body.columnconfigure(1, weight=1)

        # Token display
        tk.Frame(self, bg=SEP_COLOR, height=1).pack(fill="x", pady=(4, 0))
        tok_frame = tk.Frame(self, bg=PANEL_BG)
        tok_frame.pack(fill="x", padx=16, pady=6)

        tk.Label(tok_frame, text="Token (shown once — copy now):",
                 bg=PANEL_BG, fg=HEADER_FG, font=FONT_TINY).pack(anchor="w")
        self._token_var = tk.StringVar(value=self._token)
        tok_entry = tk.Entry(tok_frame, textvariable=self._token_var,
                             bg=PANEL_BG, fg=GREEN_FG, relief="flat",
                             font=("Consolas", 8), width=66, state="readonly",
                             readonlybackground=PANEL_BG)
        tok_entry.pack(fill="x", pady=(2, 0))

        # Buttons
        btn_bar = tk.Frame(self, bg=BG)
        btn_bar.pack(fill="x", padx=16, pady=8)

        tk.Button(btn_bar, text="⎘ Copy token + ID",
                  command=self._copy_credentials,
                  bg="#1a2a4a", fg=ACCENT, relief="flat",
                  font=FONT_BTN, cursor="hand2", padx=10, pady=4,
                  ).pack(side="left")

        tk.Button(btn_bar, text="✕ Cancel",
                  command=self.destroy,
                  bg="#2a1a1a", fg="#cc6666", relief="flat",
                  font=FONT_BTN, cursor="hand2", padx=10, pady=4,
                  ).pack(side="right")

        tk.Button(btn_bar, text="✔ Add Client",
                  command=self._confirm,
                  bg="#1a3a1a", fg=GREEN_FG, relief="flat",
                  font=FONT_BTN, cursor="hand2", padx=10, pady=4,
                  ).pack(side="right", padx=(0, 6))

    def _regen_id(self) -> None:
        self._cid = _make_client_id()
        self._cid_var.set(self._cid)

    def _copy_credentials(self) -> None:
        cid   = self._cid_var.get().strip()
        token = self._token_var.get()
        self.clipboard_clear()
        self.clipboard_append(f"client_id: {cid}\ntoken: {token}")
        messagebox.showinfo(
            "Copied",
            "Client ID and token copied to clipboard.\n\n"
            "Paste them into the client's config file (client.json).",
            parent=self,
        )

    def _confirm(self) -> None:
        cid   = self._cid_var.get().strip()
        label = self._label_var.get().strip()
        roles = [r for r, v in self._role_vars.items() if v.get()]

        if not cid:
            messagebox.showerror("Error", "Client ID cannot be empty.",
                                 parent=self)
            return
        if self._app.registry.get(cid) is not None:
            messagebox.showerror("Error",
                                 f"Client ID '{cid}' already exists.",
                                 parent=self)
            return

        from agent.network.client_registry import ClientRecord
        record = ClientRecord(
            client_id  = cid,
            token_hash = hash_token(self._token),
            roles      = roles,
            label      = label,
        )
        self._app.registry.add(record)
        self.destroy()

    def _center_on(self, parent: tk.Misc) -> None:
        self.update_idletasks()
        px = parent.winfo_rootx() + parent.winfo_width()  // 2
        py = parent.winfo_rooty() + parent.winfo_height() // 2
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px - w // 2}+{py - h // 2}")


# ── RoleEditor ────────────────────────────────────────────────────────────────

class RoleEditor(tk.Toplevel):
    """Checkbox dialog to reassign roles for an existing client."""

    def __init__(
        self, parent: tk.Misc, app: "EDApp", record: ClientRecord
    ) -> None:
        super().__init__(parent)
        label = record.label or record.client_id
        self.title(f"Edit Roles — {label}")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()

        self._app    = app
        self._record = record
        self._build_ui()
        self._center_on(parent)

    def _build_ui(self) -> None:
        hdr = tk.Frame(self, bg=HEADER_BG, pady=4)
        hdr.pack(fill="x")
        tk.Label(hdr, text="EDIT ROLES", bg=HEADER_BG, fg=HEADER_FG,
                 font=FONT_HEAD).pack()

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=20, pady=12)

        lbl = self._record.label or self._record.client_id
        tk.Label(body, text=f"Client:  {lbl}", bg=BG, fg=ACCENT,
                 font=FONT_BODY).pack(anchor="w", pady=(0, 8))

        self._role_vars: dict[str, tk.BooleanVar] = {}
        for role in ALL_ROLES:
            var = tk.BooleanVar(value=(role in self._record.roles))
            self._role_vars[role] = var
            tk.Checkbutton(
                body,
                text=f"{_ROLE_ABBREV.get(role, role)}  ({role})",
                variable=var,
                bg=BG, fg=TEXT_FG,
                activebackground=BG, activeforeground=ACCENT,
                selectcolor=PANEL_BG, font=FONT_BODY,
            ).pack(anchor="w", pady=2)

        btn_bar = tk.Frame(self, bg=BG)
        btn_bar.pack(fill="x", padx=20, pady=(4, 12))

        tk.Button(btn_bar, text="✕ Cancel",
                  command=self.destroy,
                  bg="#2a1a1a", fg="#cc6666", relief="flat",
                  font=FONT_BTN, cursor="hand2", padx=10, pady=4,
                  ).pack(side="right")

        tk.Button(btn_bar, text="✔ Apply",
                  command=self._apply,
                  bg="#1a3a1a", fg=GREEN_FG, relief="flat",
                  font=FONT_BTN, cursor="hand2", padx=10, pady=4,
                  ).pack(side="right", padx=(0, 6))

    def _apply(self) -> None:
        roles = [r for r, v in self._role_vars.items() if v.get()]
        self._app.update_client_roles(self._record.client_id, roles)
        self.destroy()

    def _center_on(self, parent: tk.Misc) -> None:
        self.update_idletasks()
        px = parent.winfo_rootx() + parent.winfo_width()  // 2
        py = parent.winfo_rooty() + parent.winfo_height() // 2
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px - w // 2}+{py - h // 2}")


# ── RenameDialog ──────────────────────────────────────────────────────────────

class RenameDialog(tk.Toplevel):
    """Single-field dialog to set the human-readable label for a client."""

    def __init__(
        self, parent: tk.Misc, app: "EDApp", record: ClientRecord
    ) -> None:
        super().__init__(parent)
        self.title("Rename Client")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()

        self._app    = app
        self._record = record
        self._build_ui()
        self._center_on(parent)

    def _build_ui(self) -> None:
        hdr = tk.Frame(self, bg=HEADER_BG, pady=4)
        hdr.pack(fill="x")
        tk.Label(hdr, text="RENAME CLIENT", bg=HEADER_BG, fg=HEADER_FG,
                 font=FONT_HEAD).pack()

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=20, pady=12)

        tk.Label(body, text=f"ID:  {self._record.client_id}",
                 bg=BG, fg=GREY_FG, font=FONT_TINY).pack(anchor="w")

        tk.Label(body, text="Label:", bg=BG, fg=ACCENT,
                 font=FONT_BODY).pack(anchor="w", pady=(8, 2))

        self._lbl_var = tk.StringVar(value=self._record.label)
        entry = tk.Entry(body, textvariable=self._lbl_var,
                         bg=PANEL_BG, fg=TEXT_FG, insertbackground=TEXT_FG,
                         relief="flat", font=FONT_BODY, width=30)
        entry.pack(anchor="w")
        entry.focus_set()
        entry.select_range(0, "end")
        entry.bind("<Return>", lambda _: self._apply())

        btn_bar = tk.Frame(self, bg=BG)
        btn_bar.pack(fill="x", padx=20, pady=(4, 12))

        tk.Button(btn_bar, text="✕ Cancel",
                  command=self.destroy,
                  bg="#2a1a1a", fg="#cc6666", relief="flat",
                  font=FONT_BTN, cursor="hand2", padx=10, pady=4,
                  ).pack(side="right")

        tk.Button(btn_bar, text="✔ Apply",
                  command=self._apply,
                  bg="#1a3a1a", fg=GREEN_FG, relief="flat",
                  font=FONT_BTN, cursor="hand2", padx=10, pady=4,
                  ).pack(side="right", padx=(0, 6))

    def _apply(self) -> None:
        label = self._lbl_var.get().strip()
        self._app.registry.set_label(self._record.client_id, label)
        self.destroy()

    def _center_on(self, parent: tk.Misc) -> None:
        self.update_idletasks()
        px = parent.winfo_rootx() + parent.winfo_width()  // 2
        py = parent.winfo_rooty() + parent.winfo_height() // 2
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px - w // 2}+{py - h // 2}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_client_id() -> str:
    """Generate a readable random client ID like 'ed-client-a3f7'."""
    import secrets
    return "ed-client-" + secrets.token_hex(2)
