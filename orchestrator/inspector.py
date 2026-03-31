"""
Static inspection utilities for QuAIL scripts.
Parses the script into AST and infers parameter schema without executing.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .parser import Lexer, Parser

UNRESOLVED = object()


def _safe_eval(node: Any, vars_map: Dict[str, Any]) -> Any:
    if isinstance(node, (int, float, str)):
        return node
    if isinstance(node, dict):
        ntype = node.get("type")
        if ntype == "VAR":
            return vars_map.get(node.get("name"), UNRESOLVED)
        if ntype == "TUPLE":
            items = []
            for item in node.get("items", []):
                val = _safe_eval(item, vars_map)
                if val is UNRESOLVED:
                    return UNRESOLVED
                items.append(val)
            return tuple(items)
        if ntype == "BINOP":
            left = _safe_eval(node.get("left"), vars_map)
            right = _safe_eval(node.get("right"), vars_map)
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
    return UNRESOLVED


def _infer_type(value: Any) -> str:
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

    def apply_param(name: str, value: Any, dtype: Optional[str] = None) -> None:
        if name in param_schema:
            return
        schema: Dict[str, Any] = {"type": dtype or _infer_type(value)}
        if value is not UNRESOLVED:
            schema["default"] = value
            param_values[name] = value
        param_schema[name] = schema

    def walk(statements):
        nonlocal param_axes, data_axes, data_axes_effective
        for stmt in statements:
            stype = stmt.get("type")
            if stype == "PARAM":
                val = _safe_eval(stmt.get("value"), vars_map)
                if val is not UNRESOLVED:
                    vars_map[stmt.get("name")] = val
                apply_param(stmt.get("name"), val, stmt.get("dtype"))
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
                start = _safe_eval(stmt.get("start"), vars_map)
                end = _safe_eval(stmt.get("end"), vars_map)
                var = stmt.get("var")
                if var and var not in param_schema:
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
