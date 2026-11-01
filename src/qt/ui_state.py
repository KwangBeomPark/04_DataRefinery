"""Shared UI-state helpers for "Explain, don't block" micro-nudges.

When a control is disabled, the user must still learn *why* it is unavailable
and *what to do next*. These helpers bind the enabled state and the tooltip of
a widget in one call so the two can never drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtWidgets import QWidget

from src.i18n import _UI_TEXT

# Dynamic Qt property holding the current disable reason ("" when enabled).
NUDGE_REASON_PROPERTY = "nudgeReason"


@dataclass(frozen=True)
class NudgeState:
    """Snapshot of a control's availability and its explanation."""

    enabled: bool
    ready_tip: str = ""
    reason: str = ""
    action: str = ""

    def apply(self, widget: QWidget, language_code: str = "ko") -> None:
        set_enabled_with_nudge(
            widget,
            self.enabled,
            language_code=language_code,
            ready_tip=self.ready_tip,
            reason=self.reason,
            action=self.action,
        )


def format_nudge(reason: str, action: str = "", language_code: str = "ko") -> str:
    """Build a two-part tooltip: why it is disabled, then how to resolve it."""
    t = _UI_TEXT.get(language_code, _UI_TEXT["ko"])
    lines = []
    if reason:
        lines.append(f"{t.get('nudge_reason_prefix', '⛔')} {reason}")
    if action:
        lines.append(f"{t.get('nudge_action_prefix', '💡')} {action}")
    return "\n".join(lines)


def set_enabled_with_nudge(
    widget: QWidget,
    enabled: bool,
    *,
    language_code: str = "ko",
    ready_tip: str = "",
    reason: str = "",
    action: str = "",
) -> None:
    """Enable/disable ``widget`` and attach a matching explanatory tooltip.

    Enabled widgets show ``ready_tip``; disabled widgets show the reason and the
    resolving action. The tooltip is mirrored to the accessible description and
    the reason to the ``nudgeReason`` property for screen readers and tests.
    """
    widget.setEnabled(enabled)
    if enabled:
        widget.setToolTip(ready_tip)
        widget.setAccessibleDescription(ready_tip)
        widget.setProperty(NUDGE_REASON_PROPERTY, "")
        return
    tip = format_nudge(reason, action, language_code)
    widget.setToolTip(tip)
    widget.setAccessibleDescription(tip)
    widget.setProperty(NUDGE_REASON_PROPERTY, reason)


# Explicit alias for the most common call site.
set_button_enabled_with_nudge = set_enabled_with_nudge


def nudge_reason(widget: QWidget) -> Optional[str]:
    """Return the current disable reason, or ``None`` if never nudged."""
    value = widget.property(NUDGE_REASON_PROPERTY)
    return None if value is None else str(value)
