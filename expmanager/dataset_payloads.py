import time

import h5py
import numpy as np


def coerce_str_list(v):
    if isinstance(v, np.ndarray):
        v = v.tolist()
    if not isinstance(v, (list, tuple)):
        return []
    return [str(x) for x in v]


def positive_int(value, default=None):
    try:
        n = int(value)
    except Exception:
        return default
    return n if n > 0 else default


def nonnegative_int(value, default=None):
    try:
        n = int(value)
    except Exception:
        return default
    return n if n >= 0 else default


def parse_h5_slice(slice_expr: str):
    slices = []
    for part in slice_expr.split(','):
        part = part.strip()
        if part == ':' or part == '':
            slices.append(slice(None))
            continue
        if ':' in part:
            s, e = part.split(':')
            start = int(s) if s != '' else None
            stop = int(e) if e != '' else None
            slices.append(slice(start, stop))
        else:
            slices.append(int(part))
    return tuple(slices)


def apply_row_window(table: dict, limit_rows=None, offset_rows=None, *, default_tail=True):
    rows = table.get("data")
    if not isinstance(rows, list):
        return {
            **table,
            "total_rows": None,
            "returned_rows": None,
            "start_row": None,
            "end_row": None,
            "truncated": False,
            "row_count": None,
            "sent_rows": None,
        }

    total = len(rows)
    limit = positive_int(limit_rows)
    offset = nonnegative_int(offset_rows)

    if limit is None:
        start = offset if offset is not None else 0
        end = total
    elif offset is None and default_tail:
        end = total
        start = max(0, total - limit)
    else:
        start = offset if offset is not None else 0
        end = min(total, start + limit)

    start = min(max(0, start), total)
    end = min(max(start, end), total)
    windowed = rows[start:end]
    returned = len(windowed)
    truncated = start > 0 or end < total
    return {
        **table,
        "data": windowed,
        "total_rows": total,
        "returned_rows": returned,
        "start_row": start,
        "end_row": end,
        "truncated": truncated,
        # Backward-compatible aliases used by existing frontend code.
        "row_count": total,
        "sent_rows": returned,
    }


def dataset_to_point_rows(data_arr: np.ndarray, params_arr: np.ndarray | None, metadata: dict | None = None):
    """
    Convert HDF5 datasets into point rows:
      [param_0, ..., param_P-1, data_row0, ..., data_rowR-1]
    for each (save_index, data_index) point.
    """
    md = metadata or {}
    data = np.asarray(data_arr)
    if data.size == 0:
        return {"columns": coerce_str_list(md.get("param_axes", [])), "data": []}

    if data.ndim == 1:
        data = data.reshape(1, data.shape[0], 1)
    elif data.ndim == 2:
        data = data[:, :, np.newaxis]
    elif data.ndim == 3:
        expected_rows = md.get("num_data")
        try:
            expected_rows = int(expected_rows)
        except Exception:
            expected_rows = None
        if expected_rows is not None and expected_rows >= 0:
            if data.shape[0] == expected_rows:
                pass
            elif data.shape[1] == expected_rows:
                data = np.transpose(data, (1, 0, 2))
    else:
        flat = data.reshape(-1).tolist()
        return {"columns": ["value"], "data": [[v] for v in flat]}

    rows_count, width_count, saves_count = data.shape

    if params_arr is None:
        params = np.zeros((0, saves_count), dtype=float)
    else:
        params = np.asarray(params_arr)
        if params.ndim == 1:
            params = params[:, np.newaxis]
        if params.ndim != 2:
            params = params.reshape(params.shape[0], -1)
        if params.shape[1] == 0:
            params = np.zeros((params.shape[0], saves_count), dtype=float)
        elif params.shape[1] != saves_count:
            if params.shape[1] == 1:
                params = np.repeat(params, saves_count, axis=1)
            else:
                params = params[:, :saves_count]

    param_axes = coerce_str_list(md.get("param_axes", []))
    if len(param_axes) != params.shape[0]:
        param_axes = [f"param{i}" for i in range(params.shape[0])]

    data_axes = coerce_str_list(md.get("data_axes", []))
    if len(data_axes) == rows_count:
        data_cols = [name if name != "_" else f"row{i}" for i, name in enumerate(data_axes)]
    else:
        data_cols = [f"row{i}" for i in range(rows_count)]

    out_rows = []
    for s in range(saves_count):
        pvals = params[:, s].tolist() if params.shape[0] > 0 else []
        for n in range(width_count):
            dvals = data[:, n, s].tolist()
            out_rows.append(pvals + dvals)

    return {"columns": param_axes + data_cols, "data": out_rows}


def load_h5_point_table(file_path: str, metadata: dict | None = None, *, slice_expr=None, limit_rows=None, offset_rows=None):
    """
    Read an HDF5 dataset into the frontend point-row table model.

    For the native Quail shape `(data_rows, data_width, saves)`, row windows are
    loaded by save-range first so long datasets do not need to be fully realized
    before slicing.
    """
    with open_h5_read(file_path) as f:
        if "data" not in f:
            raise KeyError("Data not present in file")

        combined_meta = dict(metadata or {})
        for key in f.attrs.keys():
            value = f.attrs.get(key)
            if isinstance(value, np.ndarray):
                value = value.tolist()
            combined_meta[key] = value

        data_ds = f["data"]
        params_ds = f["params"] if "params" in f else None

        if slice_expr:
            arr = np.array(data_ds[parse_h5_slice(slice_expr)])
            params_arr = np.array(params_ds) if params_ds is not None else None
            table = dataset_to_point_rows(arr, params_arr, combined_meta)
            return apply_row_window(table, limit_rows=limit_rows, offset_rows=offset_rows), combined_meta

        limit = positive_int(limit_rows)
        offset = nonnegative_int(offset_rows)
        if len(data_ds.shape) == 3 and int(data_ds.shape[1]) > 0:
            width = int(data_ds.shape[1])
            saves = int(data_ds.shape[2])
            total_rows = width * saves

            if limit is not None or offset is not None:
                if limit is None:
                    start_row = min(offset or 0, total_rows)
                    end_row = total_rows
                elif offset is None:
                    end_row = total_rows
                    start_row = max(0, total_rows - limit)
                else:
                    start_row = min(offset, total_rows)
                    end_row = min(total_rows, start_row + limit)

                start_save = start_row // width
                end_save = int(np.ceil(end_row / width)) if end_row > start_row else start_save
                arr = np.array(data_ds[:, :, start_save:end_save])
                params_arr = np.array(params_ds[:, start_save:end_save]) if params_ds is not None else None
                table = dataset_to_point_rows(arr, params_arr, combined_meta)
                local_start = start_row - (start_save * width)
                local_end = local_start + max(0, end_row - start_row)
                rows = table.get("data", [])
                rows = rows[local_start:local_end] if isinstance(rows, list) else rows
                returned = len(rows) if isinstance(rows, list) else None
                table = {
                    **table,
                    "data": rows,
                    "total_rows": total_rows,
                    "returned_rows": returned,
                    "start_row": start_row,
                    "end_row": end_row,
                    "truncated": start_row > 0 or end_row < total_rows,
                    "row_count": total_rows,
                    "sent_rows": returned,
                }
                return table, combined_meta

        arr = np.array(data_ds)
        params_arr = np.array(params_ds) if params_ds is not None else None
        table = dataset_to_point_rows(arr, params_arr, combined_meta)
        return apply_row_window(table, limit_rows=limit_rows, offset_rows=offset_rows), combined_meta


def save_event_to_point_rows(params, data, metadata: dict | None = None):
    md = metadata or {}
    params_arr = np.asarray(params, dtype=float)
    if params_arr.ndim == 0:
        params_arr = params_arr.reshape(1)
    data_arr = np.asarray(data, dtype=float)
    if data_arr.ndim == 1:
        data_arr = data_arr.reshape(1, data_arr.shape[0], 1)
    elif data_arr.ndim == 2:
        data_arr = data_arr[:, :, np.newaxis]
    else:
        raise ValueError(f"Live save payload must be 1D or 2D, got {data_arr.shape}")
    if params_arr.ndim == 1:
        params_arr = params_arr[:, np.newaxis]
    return dataset_to_point_rows(data_arr, params_arr, md)


def is_transient_h5_open_error(err: Exception) -> bool:
    msg = str(err).lower()
    if isinstance(err, OSError) and getattr(err, "errno", None) == 11:
        return True
    transient_markers = (
        "unable to lock file",
        "resource temporarily unavailable",
        "truncated file",
        "stored_eof",
        "eof =",
        "file locking flag values don't match",
    )
    return any(marker in msg for marker in transient_markers)


def open_h5_read(path: str, retries: int = 8, base_delay: float = 0.05):
    """
    Open an HDF5 file for read while tolerating transient lock contention.
    """
    last_error = None
    for i in range(retries):
        for mode in ("nolock", "default"):
            try:
                if mode == "nolock":
                    try:
                        return h5py.File(path, "r", locking=False)
                    except TypeError:
                        return h5py.File(path, "r")
                return h5py.File(path, "r")
            except OSError as e:
                msg = str(e).lower()
                if "locking flag values don" in msg and mode == "nolock":
                    last_error = e
                    continue
                if is_transient_h5_open_error(e):
                    last_error = e
                    break
                raise
        time.sleep(base_delay * (2 ** i))
    if last_error is not None:
        raise last_error
    raise OSError(f"Failed to open HDF5 file for reading: {path}")
