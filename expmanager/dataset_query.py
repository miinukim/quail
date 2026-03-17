import ast
import re
from dataclasses import dataclass


class DatasetQueryError(ValueError):
    pass


@dataclass
class SelectItem:
    expr: str
    alias: str


@dataclass
class AggItem:
    func: str
    expr: str | None
    alias: str


@dataclass
class OrderItem:
    key: str
    desc: bool = False


def _split_top_level(value: str, sep: str = ",") -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(value):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == sep and depth == 0:
            part = value[start:i].strip()
            if part:
                parts.append(part)
            start = i + 1
    tail = value[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _normalize_lines(text: str) -> list[str]:
    chunks: list[str] = []
    for raw in text.replace(";", "\n").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        chunks.append(line)
    return chunks


def _alias_for_expr(expr: str) -> str:
    candidate = re.sub(r"[^A-Za-z0-9_]+", "_", expr.strip()).strip("_")
    return candidate or "value"


def _parse_alias(value: str) -> tuple[str, str]:
    match = re.match(r"(?is)^(.*?)(?:\s+AS\s+([A-Za-z_][A-Za-z0-9_]*))?$", value.strip())
    if not match:
        raise DatasetQueryError(f"Invalid expression: {value}")
    expr = (match.group(1) or "").strip()
    alias = (match.group(2) or "").strip() or _alias_for_expr(expr)
    if not expr:
        raise DatasetQueryError(f"Invalid expression: {value}")
    return expr, alias


def _parse_select(value: str) -> list[SelectItem]:
    items: list[SelectItem] = []
    for part in _split_top_level(value):
        expr, alias = _parse_alias(part)
        items.append(SelectItem(expr=expr, alias=alias))
    return items


def _parse_group_by(value: str) -> list[SelectItem]:
    items: list[SelectItem] = []
    for part in _split_top_level(value):
        expr, alias = _parse_alias(part)
        items.append(SelectItem(expr=expr, alias=alias))
    return items


def _parse_agg(value: str) -> list[AggItem]:
    items: list[AggItem] = []
    for part in _split_top_level(value):
        expr, alias = _parse_alias(part)
        match = re.match(r"(?is)^([A-Za-z_][A-Za-z0-9_]*)\((.*)\)$", expr)
        if not match:
            raise DatasetQueryError(f"Invalid aggregate: {part}")
        func = match.group(1).strip().lower()
        arg = match.group(2).strip()
        if func == "count" and arg in ("", "*"):
            arg = None
        elif not arg:
            raise DatasetQueryError(f"Aggregate {func} requires an argument")
        items.append(AggItem(func=func, expr=arg, alias=alias))
    return items


def _parse_order_by(value: str) -> list[OrderItem]:
    items: list[OrderItem] = []
    for part in _split_top_level(value):
        tokens = part.split()
        if not tokens:
            continue
        key = tokens[0]
        direction = tokens[1].upper() if len(tokens) > 1 else "ASC"
        items.append(OrderItem(key=key, desc=direction == "DESC"))
    return items


def parse_query(text: str) -> dict:
    query = {
        "select": [],
        "where": None,
        "group_by": [],
        "agg": [],
        "order_by": [],
        "limit": None,
    }
    for line in _normalize_lines(text):
        upper = line.upper()
        if upper.startswith("SELECT "):
            query["select"] = _parse_select(line[7:].strip())
        elif upper.startswith("WHERE "):
            query["where"] = line[6:].strip()
        elif upper.startswith("GROUP BY "):
            query["group_by"] = _parse_group_by(line[9:].strip())
        elif upper.startswith("AGG "):
            query["agg"] = _parse_agg(line[4:].strip())
        elif upper.startswith("ORDER BY "):
            query["order_by"] = _parse_order_by(line[9:].strip())
        elif upper.startswith("LIMIT "):
            try:
                query["limit"] = max(0, int(line[6:].strip()))
            except Exception as exc:
                raise DatasetQueryError("LIMIT must be an integer") from exc
        else:
            raise DatasetQueryError(f"Unknown clause: {line}")
    return query


def _safe_round(value, digits=0):
    return round(value, int(digits))


_SAFE_FUNCS = {
    "abs": abs,
    "round": _safe_round,
    "min": min,
    "max": max,
}


def _eval_expr(expr: str, row: dict):
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise DatasetQueryError(f"Invalid expression: {expr}") from exc
    return _eval_node(tree.body, row)


def _eval_node(node, row: dict):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in row:
            return row[node.id]
        if node.id in ("true", "True"):
            return True
        if node.id in ("false", "False"):
            return False
        available = ", ".join(sorted(str(k) for k in row.keys()))
        if available:
            raise DatasetQueryError(f"Unknown field: {node.id}. Available fields: {available}")
        raise DatasetQueryError(f"Unknown field: {node.id}")
    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, row)
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return +operand
        if isinstance(node.op, ast.Not):
            return not operand
    if isinstance(node, ast.BoolOp):
        values = [_eval_node(v, row) for v in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        if isinstance(node.op, ast.Or):
            return any(values)
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, row)
        right = _eval_node(node.right, row)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.Mod):
            return left % right
        if isinstance(node.op, ast.Pow):
            return left ** right
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, row)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, row)
            ok = True
            if isinstance(op, ast.Eq):
                ok = left == right
            elif isinstance(op, ast.NotEq):
                ok = left != right
            elif isinstance(op, ast.Lt):
                ok = left < right
            elif isinstance(op, ast.LtE):
                ok = left <= right
            elif isinstance(op, ast.Gt):
                ok = left > right
            elif isinstance(op, ast.GtE):
                ok = left >= right
            else:
                raise DatasetQueryError("Unsupported comparison")
            if not ok:
                return False
            left = right
        return True
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise DatasetQueryError("Unsupported function call")
        func_name = node.func.id
        func = _SAFE_FUNCS.get(func_name)
        if func is None:
            raise DatasetQueryError(f"Unsupported function: {func_name}")
        args = [_eval_node(arg, row) for arg in node.args]
        return func(*args)
    if isinstance(node, ast.Attribute):
        raise DatasetQueryError("Attribute access is not allowed")
    raise DatasetQueryError("Unsupported expression")


def _to_row_dict(columns: list[str], raw_row: list) -> dict:
    return {col: raw_row[idx] if idx < len(raw_row) else None for idx, col in enumerate(columns)}


def _apply_aggregate(func: str, expr: str | None, rows: list[dict]):
    if func == "count":
        return len(rows)
    if expr is None:
        raise DatasetQueryError(f"Aggregate {func} requires an expression")
    if func == "count_if":
        return sum(1 for row in rows if bool(_eval_expr(expr, row)))
    values = [_eval_expr(expr, row) for row in rows]
    if func == "sum":
        return sum(values)
    if func in ("avg", "average"):
        return sum(values) / len(values) if values else None
    if func == "min":
        return min(values) if values else None
    if func == "max":
        return max(values) if values else None
    if func == "first":
        return values[0] if values else None
    if func == "last":
        return values[-1] if values else None
    raise DatasetQueryError(f"Unsupported aggregate: {func}")


def execute_query(columns: list[str], rows: list[list], query_text: str):
    query = parse_query(query_text)
    row_dicts = [_to_row_dict(columns, row) for row in rows]

    where_expr = query["where"]
    if where_expr:
        row_dicts = [row for row in row_dicts if bool(_eval_expr(where_expr, row))]

    group_items: list[SelectItem] = query["group_by"]
    agg_items: list[AggItem] = query["agg"]
    select_items: list[SelectItem] = query["select"]

    out_columns: list[str]
    out_rows: list[list]

    if group_items:
        grouped: dict[tuple, list[dict]] = {}
        for row in row_dicts:
            key = tuple(_eval_expr(item.expr, row) for item in group_items)
            grouped.setdefault(key, []).append(row)
        out_columns = [item.alias for item in group_items] + [item.alias for item in agg_items]
        out_rows = []
        for key, group_rows in grouped.items():
            values = list(key)
            for agg in agg_items:
                values.append(_apply_aggregate(agg.func, agg.expr, group_rows))
            out_rows.append(values)
        if not agg_items:
            out_columns = [item.alias for item in group_items]
            out_rows = [list(key) for key in grouped.keys()]
    elif select_items:
        out_columns = [item.alias for item in select_items]
        out_rows = [[_eval_expr(item.expr, row) for item in select_items] for row in row_dicts]
    else:
        out_columns = list(columns)
        out_rows = [[row.get(col) for col in out_columns] for row in row_dicts]

    order_items: list[OrderItem] = query["order_by"]
    if order_items and out_rows:
        col_index = {name: idx for idx, name in enumerate(out_columns)}
        for item in reversed(order_items):
            idx = col_index.get(item.key)
            if idx is None:
                raise DatasetQueryError(f"Unknown ORDER BY field: {item.key}")
            out_rows.sort(key=lambda row: (row[idx] is None, row[idx]), reverse=item.desc)

    limit = query["limit"]
    if isinstance(limit, int):
        out_rows = out_rows[:limit]

    return {
        "columns": out_columns,
        "data": out_rows,
        "query": {
            "text": query_text,
            "grouped": bool(group_items),
            "aggregated": bool(agg_items),
            "row_count": len(out_rows),
        },
    }
