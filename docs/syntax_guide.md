# QuAIL Experiment Syntax Guide

This document describes the custom syntax used for writing experiment scripts in the QuAIL system.

## Overview

The QuAIL syntax is a simple, imperative language designed for controlling physics experiments. It supports parameter management, sequencer execution, data saving, and basic control flow (loops and conditionals).

## Commands

### 1. Parameter Definition
Define a UI-exposed parameter. Parameters are typed and must end with semicolons.
Use `dynp` for internal/dynamic parameters that should not be exposed in the panel schema.

**Syntax:**
```text
param <type> <name> = <value_or_expression>;
param <type> <name> = {value = <value_or_expression>, <field> = <metadata_value>, ...};
dynp <name> = <value_or_expression>;
```

Supported parameter types are:

```text
int, float, bool, string, enum, scannable, number, auto
```

Parameter metadata must use the brace object form shown above. Fields are comma-separated.
Old space-separated metadata such as `param float x = 1 unit = "MHz";` is invalid.
The brace form must include `value`.

**Example:**
```text
param int shots = 100;
param float detuning = {value = 1.25, unit = "MHz", scale = 1000000, step = 1000, min = -100000000, max = 100000000, ndecimals = 3};
param bool use_cooling = true;
param enum state = {value = cool, choices = ("cool", "detect")};
param scannable pulse_scan = {value = RangeScan(start=0, stop=20, npoints=11), unit = "us", scale = 1};
dynp phase_offset = get device.phase;
```

Numeric metadata is exposed to MAQPIE as panel schema. `scale` is stored in backend units:
the UI displays `value / scale` and sends `display_value * scale` back when queueing a run.
Scannable metadata is also exposed to MAQPIE, but the scan type is locked to the type declared
by experiment code. The UI can edit fields within the declared scan object, not switch
`RangeScan` to `CenterScan`, etc.

### 2. Setting Values
Set a value to a parameter or a local variable.

**Syntax:**
```text
set <parameter_or_variable> <value>;
```

**Example:**
```text
# Set instrument parameter directly
set keithley.voltage 1.5;

# Set local variable
set my_var 10;
```

### 3. Getting Values
Fetch a value from an instrument parameter.

**Syntax:**
```text
get <alias_or_path>
```

### 4. Running the Sequencer
Trigger the FPGA sequencer and capture returned data. The return value is a single numeric data object,
usually an `R x N` row-major array where `R` is the number of data rows declared by `init_data(data=...)`.

**Syntax:**
```text
run(<seq_name>, <seq_code>)
# OR
<data> = run(<seq_name>, <seq_code>)
```

**Example:**
```text
# Just run
run("seq1", "code.py");

# Run and capture data
d = run("seq1", "code.py");
```

### 5. Initializing Data
Define the structure of the data table before saving. This must be called before any `save()` command.
You can define both parameter axes (`param`) and row labels for sequencer outputs (`data`).

**Syntax:**
```text
init_data(param=(<axis1>, <axis2>, ...), data=(<row1>, <row2>, ...));
```

**Example:**
```text
init_data(param=("voltage", "current"), data=("x", "y", "z", _));

# Notes
# - `data` length defines expected row count for each save block.
# - Use `_` as an unlabeled placeholder row.
# - Metadata stores:
#   param_axes, data_axes, data_axes_effective (without `_`)
```

### 6. Saving Data
Save data points to the experiment log/database. The arguments must match the initialized schema.
When `init_data(data=...)` is given, `save(..., data=...)` should have the same number of rows.
The processor drops empty rows and truncates extra rows to the declared row count to tolerate
sequencer payloads with unused trailing rows, but experiments should still declare only the rows
that should be saved.

**Syntax:**
```text
save(params=(<val1>, <val2>, ...), data=<data_object>);
```

**Example:**
```text
save(params=(v, i), data=d);
```

### 7. Timing
Pause execution for a specified duration (in seconds).

**Syntax:**
```text
sleep(<seconds>);
```

**Example:**
```text
sleep(0.5);
```

### 8. Loops
Iterate over a range of values or over a scannable parameter.

**Syntax:**
```text
for <variable> in (from <start> to <end> step <step_size>) : {
    <statements>
}

for <variable> in <scannable_parameter> : {
    <statements>
}
```

**Example:**
```text
for v in (from 0 to 10 step 0.1) : {
    set dac v;
    sleep(0.01);
}

param scannable pulse_scan = {value = RangeScan(start=0, stop=20, npoints=11, randomize=false), unit = "us", scale = 1};
for pulse_time in pulse_scan : {
    set dds.pulse_time_us pulse_time;
}
```

Regular range loops are fixed by the experiment code and are not editable from MAQPIE.
Use a `param scannable` only when the experiment intentionally exposes scan values to the UI:

```text
param scannable pulse_scan = {value = RangeScan(start=0, stop=20, npoints=11), unit = "us", scale = 1};
for pulse_time in pulse_scan : {
    set dds.pulse_time_us pulse_time;
}
```

Supported scan constructors are:

```text
NoScan(value=<number>, repetitions=<int>)
RangeScan(start=<number>, stop=<number>, npoints=<int>, randomize=<bool>, seed=<int>)
CenterScan(center=<number>, span=<number>, step=<number>, randomize=<bool>, seed=<int>)
ExplicitScan(sequence=(<number>, <number>, ...))
```

`randomize` and `seed` are optional for `RangeScan` and `CenterScan`.

### 9. Conditional Logic
Execute statements based on a condition.

**Syntax:**
```text
if (<condition>) : {
    <statements>
} else : {
    <statements>
}
```

**Supported Comparisons:** `==`, `!=`, `<`, `<=`, `>`, `>=`

**Example:**
```text
if (v > 2.5) : {
    set dac 0;
} else : {
    set dac v;
}
```

## Example Script
```text
# Initialize data table schema
# param: tuple of parameter-axis names
# data: tuple of row labels for sequencer output rows
init_data(param=("v", "i"), data=("x", "y", "z", _));

# Define a threshold
param float threshold = 3.0;

for v in (from -5 to 5 step 0.5) : {
    # Conditional logic
    if (v > threshold) : {
        set dac threshold;
    } else : {
        set dac v;
    }
    
    # Wait for settle
    sleep(0.1);
    
    # Get parameter value
    i = get device2.voltage;
    
    # Run sequencer with arguments and capture data
    d = run("seq1", "some_code.py");
    
    # Save data matching the initialized schema
    # params: tuple of scalar values for axes
    # data: single data object (RxN array), where R matches init_data(data=...)
    save(params=(v, i), data=d);
}
```
