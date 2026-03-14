"""
ED Assist — Exobiology Role
=============================
Filters Elite Dangerous journal events relevant to exobiology activities
and prepares them for forwarding to subscribed clients.

Events handled
--------------
  ScanOrganic       — player performed one scan step on an organic sample.
                      Each species requires 3 scans; the ``ScanType`` field
                      distinguishes Log (1st), Sample (2nd), and Analyse (3rd).
  SellOrganicData   — player sold exobiology data at Vista Genomics.
  CodexEntry        — new codex entry; we forward only biology-category entries.

Wire payload shapes
-------------------
  ScanOrganic →
    {
      "event":        "ScanOrganic",
      "body":         "<body name>",          # planet/moon
      "species":      "<localised species>",  # e.g. "Bacterium Aurasus"
      "variant":      "<localised variant>",  # e.g. "Teal"  (may be "")
      "scan_type":    "Log" | "Sample" | "Analyse",
      "system":       "<system name>",
      "value":        <int cr>,               # 0 if not yet known
    }

  SellOrganicData →
    {
      "event":        "SellOrganicData",
      "total_value":  <int cr>,
      "items": [
        { "species": "<localised>", "value": <int cr>, "bonus": <int cr> },
        ...
      ],
    }

  CodexEntry (biology only) →
    {
      "event":        "CodexEntry",
      "entry_id":     <int>,
      "name":         "<entry name>",
      "category":     "<category>",
      "system":       "<system>",
      "body":         "<body>",
      "is_new_entry": <bool>,
    }
"""
from __future__ import annotations

from agent.roles.base_role import BaseRole
from shared.roles_def import Role

# CodexEntry categories that belong to biology
_BIO_CATEGORIES: frozenset[str] = frozenset({
    "$Codex_Category_Biology;",
    "Biology",
})


class ExobiologyRole(BaseRole):
    """Exobiology role — filters and enriches organic-scan journal events."""

    name = Role.EXOBIOLOGY
    journal_events = frozenset({
        "ScanOrganic",
        "SellOrganicData",
        "CodexEntry",
    })

    def filter(self, event_name: str, data: dict) -> dict | None:
        if event_name == "ScanOrganic":
            return self._handle_scan_organic(data)
        if event_name == "SellOrganicData":
            return self._handle_sell_organic(data)
        if event_name == "CodexEntry":
            return self._handle_codex_entry(data)
        return None

    # ── Event handlers ─────────────────────────────────────────────────────

    @staticmethod
    def _handle_scan_organic(data: dict) -> dict:
        species_raw  = data.get("Species_Localised") or data.get("Species", "")
        variant_raw  = data.get("Variant_Localised")  or data.get("Variant", "")
        body         = data.get("Body", "")
        system       = data.get("SystemName", "")
        scan_type    = data.get("ScanType", "")

        # Estimated value: if the game supplies it use it, otherwise 0
        value = int(data.get("SurveyData", {}).get("Value", 0)
                    if isinstance(data.get("SurveyData"), dict) else 0)

        return {
            "event":     "ScanOrganic",
            "body":      body,
            "species":   species_raw,
            "variant":   variant_raw,
            "scan_type": scan_type,
            "system":    system,
            "value":     value,
        }

    @staticmethod
    def _handle_sell_organic(data: dict) -> dict:
        items = []
        for entry in data.get("BioData", []):
            items.append({
                "species": (entry.get("Species_Localised")
                            or entry.get("Species", "")),
                "value":   int(entry.get("Value", 0)),
                "bonus":   int(entry.get("Bonus", 0)),
            })
        return {
            "event":       "SellOrganicData",
            "total_value": int(data.get("TotalEarnings", 0)),
            "items":       items,
        }

    @staticmethod
    def _handle_codex_entry(data: dict) -> dict | None:
        category = data.get("Category_Localised") or data.get("Category", "")
        if category not in _BIO_CATEGORIES:
            return None   # not a biology entry — drop
        return {
            "event":        "CodexEntry",
            "entry_id":     data.get("EntryID", 0),
            "name":         (data.get("Name_Localised")
                             or data.get("Name", "")),
            "category":     category,
            "system":       data.get("System", ""),
            "body":         data.get("NearestDestination_Localised")
                            or data.get("NearestDestination", ""),
            "is_new_entry": bool(data.get("IsNewEntry", False)),
        }
