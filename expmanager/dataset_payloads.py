import time

import h5py
import numpy as np


def coerce_str_list(v):
    if isinstance(v, np.ndarray):
        v = v.tolist()
    if not isinstance(v, (list, tuple)):
        return []
    return [str(x) for x in v]


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
