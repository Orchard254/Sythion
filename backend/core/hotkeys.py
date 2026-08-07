"""
GUI hotkey policy: the canonical whitelist of keys `gui_hotkey` may synthesize.

This lives in `core` (not `api/schemas` or `tools`) so the schema description
the model sees and the runtime whitelist cannot drift apart, without polluting
the DTO layer (`api/schemas`) with policy data or creating a schemas<->tools
cycle. `core` is the bottom layer: neither `api` nor `tools` imports it today,
so this module must stay free of imports from either.
"""

# Hotkeys Sythion (Synth) never synthesizes, even when a window is foreground.
DANGEROUS_HOTKEYS = frozenset({
    ("alt", "ctrl", "delete"),
    ("ctrl", "esc", "shift"),
    ("l", "win"),
})

# The full set of keys gui_hotkey may synthesize. Runtime enforcement in
# tools/gui_tools and the model-facing schema description both derive from this.
HOTKEY_KEYS = frozenset({
    "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m",
    "n", "o", "p", "q", "r", "s", "t", "u", "v", "w", "x", "y", "z",
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
    "ctrl", "alt", "shift", "win", "enter", "return", "tab", "space", "backspace",
    "delete", "escape", "esc", "home", "end", "up", "down", "left", "right",
    "volumeup", "volumedown", "mute",
    "-", "=", "[", "]", ";", "'", ",", ".", "/", "\\",
})

HOTKEY_KEYS_DESC = (
    "JSON array (list) of key names pressed together as a combination, "
    "e.g. [\"ctrl\", \"shift\", \"esc\"]. Allowed keys: "
    + ", ".join(sorted(HOTKEY_KEYS)) + "."
)
