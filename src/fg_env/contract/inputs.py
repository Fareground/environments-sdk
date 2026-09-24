"""Validate caller-supplied inputs against the contract's declared inputs."""
from __future__ import annotations

import copy
import csv
import datetime as _dt
import io
import json
import math
import os
from fractions import Fraction
from difflib import get_close_matches
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

from . import Contract, InputSpec
from ..errors import InputError, Issue

__all__ = ["resolve_inputs", "check_value", "DATA_SUFFIXES", "MAX_DATA_BYTES", "MAX_DATA_ROWS"]

#: Data file kinds an input `source` may name.
DATA_SUFFIXES = (".csv", ".json", ".jsonl")
#: Largest data file read for one input, and most rows kept from it.
MAX_DATA_BYTES = 50_000_000
MAX_DATA_ROWS = 1_000_000


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def check_value(type_name: str, value: Any, spec: Optional[InputSpec] = None) -> Optional[str]:
    """Return a problem description, or None when ``value`` fits ``type_name``."""
    if type_name == "any":
        return None
    if type_name == "number":
        if not _is_number(value):
            return f"must be a number, got {value!r}"
    elif type_name == "int":
        if not _is_number(value) or float(value) != int(value):
            return f"must be a whole number, got {value!r}"
    elif type_name == "bool":
        if not isinstance(value, bool):
            return f"must be true or false, got {value!r}"
    elif type_name == "text":
        if not isinstance(value, str):
            return f"must be text, got {value!r}"
    elif type_name == "enum":
        allowed = (spec.values if spec else None) or []
        if value not in allowed:
            return f"must be one of {allowed}, got {value!r}"
    elif type_name == "list":
        if not isinstance(value, list):
            return f"must be a list, got {type(value).__name__}"
    elif type_name == "map":
        if not isinstance(value, dict):
            return f"must be an object, got {type(value).__name__}"
    elif type_name == "date":
        if not isinstance(value, str):
            return f"must be an ISO date text like 2026-01-31, got {value!r}"
        try:
            _dt.date.fromisoformat(value[:10])
        except ValueError:
            return f"must be an ISO date like 2026-01-31, got {value!r}"
    elif type_name == "table":
        if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
            return "must be a list of rows (objects)"
        columns = (spec.columns if spec else None) or {}
        for index, row in enumerate(value):
            for column, column_type in columns.items():
                if column not in row:
                    return f"row {index} is missing column '{column}'"
                problem = check_value("text" if column_type == "asset" else column_type, row[column], (spec.fields or {}).get(column) if spec else None)
                if problem:
                    return f"row {index} column '{column}' {problem}"
    else:
        return f"has unknown type '{type_name}'"
    if spec is not None and spec.fields is not None and type_name in {"map", "table"}:
        rows = enumerate(value) if type_name == "table" else [(None, value)]
        for position, row in rows:
            for name, field in spec.fields.items():
                prefix = f"row {position} field '{name}'" if position is not None else f"field '{name}'"
                if name not in row and field.default is None:
                    if field.required:
                        return f"{prefix} is required"
                    continue
                item = row.get(name, field.default)
                problem = check_value(field.type, item, field)
                if problem:
                    return f"{prefix} {problem}"
    if spec is not None and spec.items is not None and type_name == "list":
        for index, item in enumerate(value):
            problem = check_value(spec.items.type, item, spec.items)
            if problem:
                return f"item {index} {problem}"
    if spec is not None and _is_number(value):
        if spec.min is not None and value < spec.min:
            return f"must be ≥ {spec.min:g}, got {value}"
        if spec.max is not None and value > spec.max:
            return f"must be ≤ {spec.max:g}, got {value}"
        if spec.multiple_of is not None:
            # Decimal spelling avoids binary modulo errors; a tiny fraction of
            # one increment tolerates ordinary arithmetic noise such as .1+.2.
            units = Fraction(str(value)) / Fraction(str(spec.multiple_of))
            if abs(units - round(units)) > Fraction(1, 1_000_000_000):
                return f"must be a multiple of {spec.multiple_of:g}, got {value}"
    return None


def resolve_inputs(contract: Contract, supplied: Optional[Mapping[str, Any]] = None,
                   data_dir: Union[str, "os.PathLike[str]", None] = None) -> Dict[str, Any]:
    """Merge supplied inputs over data files over defaults. Raises :class:`InputError` listing every problem."""
    supplied = dict(supplied or {})
    issues: List[Issue] = []
    declared = contract.inputs
    for name in supplied:
        if name not in declared:
            hint = get_close_matches(name, list(declared), n=1)
            issues.append(Issue(
                f"inputs.{name}", "is not a declared input",
                f"did you mean '{hint[0]}'?" if hint else f"declared inputs: {', '.join(declared) or 'none'}",
            ))
    resolved: Dict[str, Any] = {}
    for name, spec in declared.items():
        if name in supplied:
            value = supplied[name]
        elif spec.source is not None:
            try:
                value = load_source(spec, data_dir)
            except _SourceProblem as failure:
                issues.append(Issue(f"inputs.{name}.source", str(failure), failure.fix))
                continue
        elif spec.default is not None:
            value = copy.deepcopy(spec.default)
        elif spec.required:
            issues.append(Issue(f"inputs.{name}", "is required", f"supply inputs={{'{name}': ...}}"))
            continue
        else:
            value = None
        if value is not None:
            problem = check_value(spec.type, value, spec)
            if problem:
                issues.append(Issue(f"inputs.{name}", problem, spec.description or None))
                continue
            if spec.type == "int":
                value = int(value)
        resolved[name] = _nested_defaults(spec, value)
    if issues:
        raise InputError(issues)
    return resolved


def _nested_defaults(spec: InputSpec, value: Any) -> Any:
    """Materialize declared child defaults without changing the caller's data."""
    fields = spec.fields
    if fields is not None and value is not None:
        def row_defaults(row: Dict[str, Any]) -> Dict[str, Any]:
            result = copy.deepcopy(row)
            for name, field in fields.items():
                if name in result or field.default is not None:
                    result[name] = _nested_defaults(field, result.get(name, copy.deepcopy(field.default)))
            return result
        return [row_defaults(row) for row in value] if spec.type == "table" else row_defaults(value)
    if spec.items is not None and isinstance(value, list):
        return [_nested_defaults(spec.items, item) for item in value]
    return copy.deepcopy(value)


class _SourceProblem(Exception):
    def __init__(self, message: str, fix: Optional[str] = None):
        super().__init__(message)
        self.fix = fix


def load_source(spec: InputSpec, data_dir: Union[str, "os.PathLike[str]", None]) -> Any:
    """Read an input's data file. Only files inside ``data_dir`` are read: absolute paths, `..`,
    and links that lead outside it are refused, as are unknown file kinds and oversized files."""
    name = spec.source or ""
    if data_dir is None:
        raise _SourceProblem(f"'{name}' needs a data directory",
                             "load the contract from its file (its folder is used) or pass data_dir=")
    relative = Path(name)
    if not name or "\x00" in name or relative.is_absolute() or ".." in relative.parts:
        raise _SourceProblem(f"'{name}' must be a file name inside the data directory",
                             "use a relative path without '..'")
    suffix = relative.suffix.lower()
    if suffix not in DATA_SUFFIXES:
        raise _SourceProblem(f"'{name}' is not a supported data file", f"use one of: {', '.join(DATA_SUFFIXES)}")
    base = Path(data_dir).resolve()
    path = (base / relative).resolve()
    if base != path and base not in path.parents:
        raise _SourceProblem(f"'{name}' leads outside the data directory", "keep data files inside it")
    if not path.is_file():
        raise _SourceProblem(f"file not found: '{name}' in {base}", "check the name and the data directory")
    size = path.stat().st_size
    if size > MAX_DATA_BYTES:
        raise _SourceProblem(f"'{name}' is {size:,} bytes; the limit is {MAX_DATA_BYTES:,}", "use a smaller extract")
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _SourceProblem(f"'{name}' is not UTF-8 text (invalid byte at position {exc.start})",
                             "save it as UTF-8") from None
    if suffix == ".csv":
        if spec.type != "table":
            raise _SourceProblem(f"a CSV file gives a table, but this input is {spec.type}", "set type: table")
        return _csv_rows(text, spec.columns or {}, name, spec.fields)
    try:
        if suffix == ".json":
            return json.loads(text)
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        raise _SourceProblem(f"'{name}' is not valid JSON: {exc.msg} at line {exc.lineno}", "fix the file") from None
    if len(rows) > MAX_DATA_ROWS:
        raise _SourceProblem(f"'{name}' has {len(rows):,} rows; the limit is {MAX_DATA_ROWS:,}", "use a smaller extract")
    return rows


def _csv_rows(text: str, columns: Mapping[str, str], name: str, fields: Optional[Dict[str, InputSpec]] = None) -> List[Dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise _SourceProblem(f"'{name}' has no header row", "put column names on the first line")
    missing = [column for column in columns if column not in reader.fieldnames]
    if missing:
        raise _SourceProblem(f"'{name}' has no column(s) {', '.join(missing)} (columns: {', '.join(reader.fieldnames)})",
                             "match `columns` to the file's header")
    rows: List[Dict[str, Any]] = []
    for line, raw in enumerate(reader, start=2):
        if len(rows) >= MAX_DATA_ROWS:
            raise _SourceProblem(f"'{name}' has more than {MAX_DATA_ROWS:,} rows", "use a smaller extract")
        row: Dict[str, Any] = {}
        for column, cell in raw.items():
            if column is None:
                raise _SourceProblem(f"'{name}' line {line} has more cells than the header", "fix the row")
            kind = fields[column].type if fields and column in fields else columns.get(column, "text")
            row[column] = _cell(kind, cell, name, line, column)
        rows.append(row)
    return rows


def _cell(kind: str, cell: Optional[str], name: str, line: int, column: str) -> Any:
    text = (cell or "").strip()
    if kind in ("text", "enum", "date", "any", "asset"):
        return cell if cell is not None else ""
    if text == "":
        return None
    try:
        if kind == "int":
            return int(text)
        if kind == "number":
            number = float(text)
            return int(number) if number.is_integer() and "." not in text and "e" not in text.lower() else number
    except ValueError:
        raise _SourceProblem(f"'{name}' line {line} column '{column}' must be {'a whole number' if kind == 'int' else 'a number'}, got {cell!r}",
                             "fix the cell or the column type") from None
    if kind == "bool":
        lowered = text.lower()
        if lowered in ("true", "yes", "1"):
            return True
        if lowered in ("false", "no", "0"):
            return False
        raise _SourceProblem(f"'{name}' line {line} column '{column}' must be true or false, got {cell!r}",
                             "use true/false, yes/no or 1/0")
    return cell
