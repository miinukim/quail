"""
Static inspection utilities for QuAIL scripts.
Parses the script into AST and infers parameter schema without executing.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .parser import Lexer, Parser

UNRESOLVED = object()


def _safe_eval(node: Any, vars_map: Dict[str, Any], *, unresolved_var_as_string: bool = False) -> Any:
    if isinstance(node, (int, float, str)):
        return node
    if isinstance(node, dict):
        ntype = node.get("type")
        if ntype == "VAR":
            if node.get("name") in vars_map:
                return vars_map.get(node.get("name"))
            return str(node.get("name")) if unresolved_var_as_string else UNRESOLVED
        if ntype == "TUPLE":
            items = []
            for item in node.get("items", []):
                val = _safe_eval(item, vars_map, unresolved_var_as_string=unresolved_var_as_string)
                if val is UNRESOLVED:
                    return UNRESOLVED
                items.append(val)
            return tuple(items)
        if ntype == "BINOP":
            left = _safe_eval(node.get("left"), vars_map, unresolved_var_as_string=unresolved_var_as_string)
            right = _safe_eval(node.get("right"), vars_map, unresolved_var_as_string=unresolved_var_as_string)
            if left is UNRESOLVED or right is UNRESOLVED:
                return UNRESOLVED
            op = node.get("op")
            try:
                if op == "EQ":
                    return left == right
                if op == "NEQ":
                    return left != right
                if op == "LT":
                    return left < right
                if op == "LTE":
                    return left <= right
                if op == "GT":
                    return left > right
                if op == "GTE":
                    return left >= right
            except Exception:
                return UNRESOLVED
        if ntype == "GET":
            return UNRESOLVED
        if ntype == "CALL":
            name = str(node.get("name") or "")
            args = []
            for item in node.get("args", []):
                val = _safe_eval(item, vars_map, unresolved_var_as_string=unresolved_var_as_string)
                if val is UNRESOLVED:
                    return UNRESOLVED
                args.append(val)
            kwargs: Dict[str, Any] = {}
            for key, item in (node.get("kwargs") or {}).items():
                val = _safe_eval(item, vars_map, unresolved_var_as_string=unresolved_var_as_string)
                if val is UNRESOLVED:
                    return UNRESOLVED
                kwargs[key] = val
            if name in {"NoScan", "RangeScan", "CenterScan", "ExplicitScan"}:
                return _scan_object(name, args, kwargs)
    return UNRESOLVED


def _scan_object(name: str, args: list[Any], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ty": name}
    if name == "NoScan":
        out["value"] = kwargs.get("value", args[0] if args else 0)
        if "repetitions" in kwargs:
            out["repetitions"] = kwargs["repetitions"]
        return out
    if name == "RangeScan":
        keys = ("start", "stop", "npoints")
        for idx, key in enumerate(keys):
            if key in kwargs:
                out[key] = kwargs[key]
            elif idx < len(args):
                out[key] = args[idx]
        if "randomize" in kwargs:
            out["randomize"] = kwargs["randomize"]
        if "seed" in kwargs:
            out["seed"] = kwargs["seed"]
        return out
    if name == "CenterScan":
        keys = ("center", "span", "step")
        for idx, key in enumerate(keys):
            if key in kwargs:
                out[key] = kwargs[key]
            elif idx < len(args):
                out[key] = args[idx]
        if "randomize" in kwargs:
            out["randomize"] = kwargs["randomize"]
        if "seed" in kwargs:
            out["seed"] = kwargs["seed"]
        return out
    if name == "ExplicitScan":
        seq = kwargs.get("sequence", args[0] if args else [])
        out["sequence"] = list(seq) if isinstance(seq, (list, tuple)) else []
        return out
    return out


def _infer_type(value: Any) -> str:
    if _is_scan_object(value):
        return "scannable"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, (list, tuple)):
        return "iterable"
    if isinstance(value, str):
        return "string"
    return "string"


def _is_scan_object(value: Any) -> bool:
    return isinstance(value, dict) and value.get("ty") in {"NoScan", "RangeScan", "CenterScan", "ExplicitScan"}


def _range_scan_default(start: Any, end: Any, step: Any) -> Dict[str, Any] | None:
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (start, end, step)):
        return None
    if float(step) == 0:
        return None
    values = []
    current = float(start)
    stop = float(end)
    delta = float(step)
    epsilon = abs(delta) / 1000.0
    if delta > 0:
        while current <= stop + epsilon:
            values.append(current)
            current += delta
    else:
        while current >= stop - epsilon:
            values.append(current)
            current += delta
    return {"ty": "ExplicitScan", "sequence": values}


def _extract_axis_names(node: Any, vars_map: Dict[str, Any]) -> list[str]:
    val = _safe_eval(node, vars_map)
    if isinstance(val, (list, tuple)) and all(isinstance(v, str) for v in val):
        return [str(v) for v in val]
    if isinstance(node, dict) and node.get("type") == "TUPLE":
        out: list[str] = []
        for item in node.get("items", []):
            if isinstance(item, str):
                out.append(item)
                continue
            if isinstance(item, dict) and item.get("type") == "VAR":
                out.append(str(item.get("name")))
                continue
            resolved = _safe_eval(item, vars_map)
            if isinstance(resolved, str):
                out.append(resolved)
                continue
            return []
        return out
    return []


def inspect_script(code: str) -> Dict[str, Any]:
    """
    Parse script and infer parameter schema and defaults.
    Returns:
      {
        "param_schema": {name: {type, default, min, max}},
        "param_values": {name: default},
        "param_axes": [...],
      }
    """
    lexer = Lexer(code)
    parser = Parser(lexer)
    ast = parser.program()

    vars_map: Dict[str, Any] = {}
    param_schema: Dict[str, Dict[str, Any]] = {}
    param_values: Dict[str, Any] = {}
    param_axes = []
    data_axes = []
    data_axes_effective = []

    def apply_param(name: str, value: Any, dtype: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> None:
        if name in param_schema:
            return
        schema: Dict[str, Any] = {"type": dtype or _infer_type(value)}
        if value is not UNRESOLVED:
            schema["default"] = value
            param_values[name] = value
        for key, node in (metadata or {}).items():
            val = _safe_eval(node, vars_map, unresolved_var_as_string=True)
            if val is not UNRESOLVED:
                if key == "choices" and isinstance(val, tuple):
                    val = list(val)
                schema[key] = val
        param_schema[name] = schema

    def walk(statements):
        nonlocal param_axes, data_axes, data_axes_effective
        for stmt in statements:
            stype = stmt.get("type")
            if stype == "PARAM":
                val = _safe_eval(stmt.get("value"), vars_map, unresolved_var_as_string=bool(stmt.get("metadata")))
                if val is not UNRESOLVED:
                    vars_map[stmt.get("name")] = val
                apply_param(stmt.get("name"), val, stmt.get("dtype"), stmt.get("metadata") or {})
            elif stype == "DYN_PARAM":
                # Dynamic params are recorded for evaluation but not exposed to schema.
                val = _safe_eval(stmt.get("value"), vars_map)
                if val is not UNRESOLVED:
                    vars_map[stmt.get("name")] = val
            elif stype == "ASSIGN":
                val = _safe_eval(stmt.get("value"), vars_map)
                if val is not UNRESOLVED:
                    vars_map[stmt.get("name")] = val
            elif stype == "FOR":
                iterable = _safe_eval(stmt.get("iterable"), vars_map) if stmt.get("iterable") is not None else UNRESOLVED
                start = _safe_eval(stmt.get("start"), vars_map)
                end = _safe_eval(stmt.get("end"), vars_map)
                step = _safe_eval(stmt.get("step"), vars_map)
                var = stmt.get("var")
                iterable_node = stmt.get("iterable")
                iterable_param_name = (
                    iterable_node.get("name")
                    if isinstance(iterable_node, dict) and iterable_node.get("type") == "VAR"
                    else None
                )
                exposes_existing_scan_param = iterable_param_name in param_schema
                if (
                    var
                    and var not in param_schema
                    and not exposes_existing_scan_param
                    and stmt.get("iterable") is not None
                ):
                    if _is_scan_object(iterable):
                        schema = {"type": "scannable", "default": iterable}
                    else:
                        default_scan = _range_scan_default(start, end, step)
                        if default_scan is not None:
                            schema = {
                                "type": "scannable",
                                "default": default_scan,
                                "global_min": start,
                                "global_max": end,
                                "global_step": step,
                            }
                        else:
                            schema = {"type": "float"}
                            if start is not UNRESOLVED:
                                schema["default"] = start
                                schema["min"] = start
                            if end is not UNRESOLVED:
                                schema["max"] = end
                    param_schema[var] = schema
                    if "default" in schema:
                        param_values[var] = schema["default"]
                walk(stmt.get("body", []))
            elif stype == "IF":
                walk(stmt.get("true_body", []))
                walk(stmt.get("false_body", []) or [])
            elif stype == "INIT_DATA":
                kwargs = stmt.get("kwargs", {})
                param_axes = _extract_axis_names(kwargs.get("param"), vars_map) or param_axes
                data_axes = _extract_axis_names(kwargs.get("data"), vars_map) or data_axes
                data_axes_effective = [name for name in data_axes if name != "_"]

    walk(ast)

    return {
        "param_schema": param_schema,
        "param_values": param_values,
        "param_axes": param_axes,
        "data_axes": data_axes,
        "data_axes_effective": data_axes_effective,
    }
