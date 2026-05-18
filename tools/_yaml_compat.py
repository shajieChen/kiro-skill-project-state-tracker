"""YAML import compatibility layer.

Tries PyYAML first. Falls back to a tiny built-in parser/emitter that handles
the subset of YAML used by this skill:

  - Block-style mappings (key: value, indented)
  - Block-style sequences (- item)
  - Scalar types: string (plain or "double-quoted" or 'single-quoted'),
    integer, float, boolean (true/false/yes/no/on/off, case-insensitive),
    null (null, ~, empty)
  - Inline flow only for empty containers: [] and {}
  - Comments starting with # (full line or trailing)

It does NOT handle: multi-line strings (| >), anchors (& *), tags (!), or
non-empty inline flow ({a: b, c: d} or [1, 2, 3]).

If status.yaml ever uses constructs outside this subset, install PyYAML:
    pip install pyyaml
"""
from __future__ import annotations

try:
    import yaml as _pyyaml  # type: ignore

    HAVE_PYYAML = True

    def load(text: str):
        return _pyyaml.safe_load(text)

    def dump(data) -> str:
        return _pyyaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)

except ImportError:
    HAVE_PYYAML = False

    # ------------------------------------------------------------------
    # Fallback parser
    # ------------------------------------------------------------------

    def _strip_comment(line: str) -> str:
        # Strip trailing comment but respect quotes.
        in_s = False
        in_d = False
        for i, ch in enumerate(line):
            if ch == "'" and not in_d:
                in_s = not in_s
            elif ch == '"' and not in_s:
                in_d = not in_d
            elif ch == "#" and not in_s and not in_d:
                return line[:i].rstrip()
        return line.rstrip()

    def _parse_scalar(s: str):
        s = s.strip()
        if s == "" or s.lower() in ("null", "~"):
            return None
        if s.lower() in ("true", "yes", "on"):
            return True
        if s.lower() in ("false", "no", "off"):
            return False
        if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
            return s[1:-1]
        if s == "[]":
            return []
        if s == "{}":
            return {}
        try:
            return int(s)
        except ValueError:
            pass
        try:
            return float(s)
        except ValueError:
            pass
        return s

    def _indent_of(line: str) -> int:
        i = 0
        while i < len(line) and line[i] == " ":
            i += 1
        return i

    def load(text: str):
        # Pre-process: drop comment-only and blank lines, remember indentation.
        raw_lines = text.splitlines()
        lines = []
        for ln in raw_lines:
            stripped = _strip_comment(ln)
            if stripped.strip() == "":
                continue
            lines.append(stripped)
        if not lines:
            return None

        pos = [0]

        def parse_block(min_indent: int):
            if pos[0] >= len(lines):
                return None
            line = lines[pos[0]]
            indent = _indent_of(line)
            if indent < min_indent:
                return None
            content = line[indent:]
            if content.startswith("- "):
                return parse_sequence(indent)
            elif content.startswith("-") and (len(content) == 1 or content[1] == " "):
                return parse_sequence(indent)
            else:
                return parse_mapping(indent)

        def parse_mapping(indent: int):
            result = {}
            while pos[0] < len(lines):
                line = lines[pos[0]]
                cur_indent = _indent_of(line)
                if cur_indent < indent:
                    break
                if cur_indent > indent:
                    # shouldn't happen at this level; skip defensively
                    pos[0] += 1
                    continue
                content = line[indent:]
                if content.startswith("- "):
                    break
                if ":" not in content:
                    pos[0] += 1
                    continue
                key, _, val = content.partition(":")
                key = key.strip()
                val = val.strip()
                pos[0] += 1
                if val == "":
                    # Nested. Peek at next line indent.
                    if pos[0] < len(lines):
                        next_indent = _indent_of(lines[pos[0]])
                        if next_indent > indent:
                            nested = parse_block(next_indent)
                            result[key] = nested if nested is not None else None
                            continue
                    result[key] = None
                else:
                    result[key] = _parse_scalar(val)
            return result

        def parse_sequence(indent: int):
            result = []
            while pos[0] < len(lines):
                line = lines[pos[0]]
                cur_indent = _indent_of(line)
                if cur_indent < indent:
                    break
                if cur_indent > indent:
                    pos[0] += 1
                    continue
                content = line[indent:]
                if not content.startswith("-"):
                    break
                after_dash = content[1:].lstrip()
                if after_dash == "":
                    # Item is a nested block on the next lines.
                    pos[0] += 1
                    if pos[0] < len(lines):
                        next_indent = _indent_of(lines[pos[0]])
                        if next_indent > indent:
                            item = parse_block(next_indent)
                            result.append(item)
                            continue
                    result.append(None)
                else:
                    # Could be "- key: value" (start of inline mapping) or "- scalar".
                    if ":" in after_dash and not (
                        (after_dash.startswith('"') and after_dash.count('"') >= 2)
                        or (after_dash.startswith("'") and after_dash.count("'") >= 2)
                    ):
                        # Treat the rest of this line as the first entry of a mapping at
                        # column (indent + 2). Synthesize by rewriting the line then re-parsing.
                        # Easier approach: build a mini mapping manually.
                        item = {}
                        key, _, val = after_dash.partition(":")
                        key = key.strip()
                        val = val.strip()
                        pos[0] += 1
                        item_indent = indent + 2  # standard "- " offset
                        if val == "":
                            if pos[0] < len(lines):
                                next_indent = _indent_of(lines[pos[0]])
                                if next_indent > item_indent:
                                    nested = parse_block(next_indent)
                                    item[key] = nested
                                else:
                                    item[key] = None
                            else:
                                item[key] = None
                        else:
                            item[key] = _parse_scalar(val)
                        # Continue collecting more mapping fields at item_indent.
                        while pos[0] < len(lines):
                            ln = lines[pos[0]]
                            ci = _indent_of(ln)
                            if ci != item_indent:
                                break
                            c = ln[item_indent:]
                            if c.startswith("-"):
                                break
                            if ":" not in c:
                                break
                            k2, _, v2 = c.partition(":")
                            k2 = k2.strip()
                            v2 = v2.strip()
                            pos[0] += 1
                            if v2 == "":
                                if pos[0] < len(lines):
                                    ni = _indent_of(lines[pos[0]])
                                    if ni > item_indent:
                                        item[k2] = parse_block(ni)
                                    else:
                                        item[k2] = None
                                else:
                                    item[k2] = None
                            else:
                                item[k2] = _parse_scalar(v2)
                        result.append(item)
                    else:
                        result.append(_parse_scalar(after_dash))
                        pos[0] += 1
            return result

        first_indent = _indent_of(lines[0])
        return parse_block(first_indent)

    # ------------------------------------------------------------------
    # Fallback emitter
    # ------------------------------------------------------------------

    def _emit_scalar(v) -> str:
        if v is None:
            return "null"
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, str):
            # Quote if it contains special chars or could be misparsed.
            if v == "":
                return '""'
            specials = set(":#-?,&*![]{}|>%@`")
            needs_quote = (
                v[0] in specials
                or v[0] == " "
                or v[-1] == " "
                or v.lower() in ("true", "false", "null", "yes", "no", "on", "off", "~")
                or any(c in v for c in (":", "#"))
            )
            try:
                int(v)
                needs_quote = True
            except ValueError:
                pass
            try:
                float(v)
                needs_quote = True
            except ValueError:
                pass
            if needs_quote:
                escaped = v.replace("\\", "\\\\").replace('"', '\\"')
                return f'"{escaped}"'
            return v
        return str(v)

    def _emit(v, indent: int) -> str:
        pad = " " * indent
        if isinstance(v, dict):
            if not v:
                return "{}"
            parts = []
            for k, val in v.items():
                if isinstance(val, (dict, list)) and val:
                    parts.append(f"{pad}{k}:\n{_emit(val, indent + 2)}")
                elif isinstance(val, (dict, list)):
                    # Empty container.
                    parts.append(f"{pad}{k}: {'{}' if isinstance(val, dict) else '[]'}")
                else:
                    parts.append(f"{pad}{k}: {_emit_scalar(val)}")
            return "\n".join(parts)
        if isinstance(v, list):
            if not v:
                return "[]"
            parts = []
            for item in v:
                if isinstance(item, dict) and item:
                    # Render first key on the dash line for compactness.
                    keys = list(item.keys())
                    first_k = keys[0]
                    first_v = item[first_k]
                    if isinstance(first_v, (dict, list)) and first_v:
                        parts.append(f"{pad}- {first_k}:\n{_emit(first_v, indent + 4)}")
                    elif isinstance(first_v, (dict, list)):
                        parts.append(f"{pad}- {first_k}: {'{}' if isinstance(first_v, dict) else '[]'}")
                    else:
                        parts.append(f"{pad}- {first_k}: {_emit_scalar(first_v)}")
                    for k in keys[1:]:
                        val = item[k]
                        sub_pad = " " * (indent + 2)
                        if isinstance(val, (dict, list)) and val:
                            parts.append(f"{sub_pad}{k}:\n{_emit(val, indent + 4)}")
                        elif isinstance(val, (dict, list)):
                            parts.append(f"{sub_pad}{k}: {'{}' if isinstance(val, dict) else '[]'}")
                        else:
                            parts.append(f"{sub_pad}{k}: {_emit_scalar(val)}")
                elif isinstance(item, list) and item:
                    parts.append(f"{pad}-\n{_emit(item, indent + 2)}")
                elif isinstance(item, (dict, list)):
                    parts.append(f"{pad}- {'{}' if isinstance(item, dict) else '[]'}")
                else:
                    parts.append(f"{pad}- {_emit_scalar(item)}")
            return "\n".join(parts)
        return f"{pad}{_emit_scalar(v)}"

    def dump(data) -> str:
        return _emit(data, 0) + "\n"
