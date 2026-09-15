"""nimRumConfigDoc — one documentation file per config file, three ways to read it.

Each config file has a single Markdown document next to the code that owns it:

    nimRum/rx/rxConfig.md
    nimRum/tx/txConfig.md
    nimRum/audio_source/audioSourceConfig.md

Every one of them is both prose (for people) and a Markdown table (for machines).
This module is the only place that reads them, and it serves three consumers from
that one source:

    load(name)             raw Markdown, for docs
    fields(name)           [{key, default, desc}], for the WebUI Config tab
    as_yaml_comments(name) a "# ..." block, for the generated .last files

Before this existed the same keys were documented in an inline help string inside
each Cfg class *and* in a separate field table for the UI, and the two had already
drifted apart.

## Table format

A document may contain several tables, each introduced by a `##` heading. The
heading text becomes the section name. A table looks like:

    | Key | Default | Description |
    |-----|---------|-------------|
    | nic | wlan0   | Network interface for audio traffic |

Rows with an empty key are skipped. Anything outside a table is prose and is only
used by `load()` and `as_yaml_comments()`.
"""

import os
import re
from typing import Dict, List, Optional

# name -> path relative to the nimRum package root
_DOCS = {
    "rxConfig": os.path.join("rx", "rxConfig.md"),
    "txConfig": os.path.join("tx", "txConfig.md"),
    "audioSourceConfig": os.path.join("audio_source", "audioSourceConfig.md"),
}

_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_SEPARATOR = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def available() -> List[str]:
    """Names that can be passed to the other functions."""
    return sorted(_DOCS)


def path(name: str) -> str:
    """Absolute path to a config document."""
    if name not in _DOCS:
        raise KeyError(f"Unknown config doc '{name}', expected one of {available()}")
    return os.path.join(_PKG_ROOT, _DOCS[name])


def load(name: str) -> str:
    """Raw Markdown for a config file. Empty string if it is missing."""
    try:
        with open(path(name), "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _split_row(line: str) -> List[str]:
    m = _TABLE_ROW.match(line)
    if not m:
        return []
    return [c.strip() for c in m.group(1).split("|")]


def fields(name: str, section: Optional[str] = None) -> List[Dict[str, str]]:
    """Parse the Markdown tables into field descriptions.

    Args:
        name: Config document name, e.g. "rxConfig".
        section: Only return rows from the table under this `##` heading.
            Defaults to every table in the document.

    Returns:
        A list of {"key", "default", "desc", "section"} dicts, in document order.
        A table with only two columns (no defaults, e.g. per-client settings)
        yields an empty "default".
    """
    out: List[Dict[str, str]] = []
    current = ""
    header: List[str] = []

    for line in load(name).splitlines():
        if line.startswith("##"):
            current = line.lstrip("#").strip()
            header = []
            continue
        if _SEPARATOR.match(line):
            continue

        cells = _split_row(line)
        if not cells:
            header = []
            continue

        lowered = [c.lower() for c in cells]
        if "key" in lowered:
            header = lowered          # this is a table header row
            continue
        if not header:
            continue                  # a table we do not understand — skip

        row = dict(zip(header, cells))
        key = row.get("key", "")
        if not key:
            continue
        if section is not None and current != section:
            continue
        out.append({
            "key": key,
            "default": row.get("default", ""),
            "desc": row.get("description", row.get("desc", "")),
            "section": current,
        })
    return out


def as_yaml_comments(name: str) -> str:
    """Render a config document as a YAML comment block.

    Used as the header of the generated `.last` files, so the file a user copies
    into place explains itself with exactly the text the UI shows.
    """
    text = load(name)
    if not text:
        return "#\n"

    lines = ["#"]
    for line in text.splitlines():
        stripped = line.rstrip()
        if not stripped:
            lines.append("#")
        else:
            lines.append("# " + stripped)
    lines.append("#")
    return "\n".join(lines) + "\n"


def get_all_references() -> Dict[str, List[Dict[str, str]]]:
    """Everything the WebUI Config tab needs, keyed by config document name."""
    return {name: fields(name) for name in available()}
