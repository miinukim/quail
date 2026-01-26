# QuAIL Experiment Syntax Guide

This document describes the custom syntax used for writing experiment scripts in the QuAIL system.

## Overview

The QuAIL syntax is a simple, imperative language designed for controlling physics experiments. It supports parameter management, sequencer execution, data saving, and basic control flow (loops and conditionals).

## Commands

### 1. Parameter Definition
Define a variable or an alias for a QCoDeS instrument parameter.

**Syntax:**
```text
param <name> = <value_or_expression>
```

**Example:**
```text
# Define a variable from a GET command
param t = get keithley.voltage
```

### 2. Setting Values
Set a value to a parameter or a local variable.

**Syntax:**
```text
set <parameter_or_variable> <value>
```

**Example:**
```text
# Set instrument parameter directly
set keithley.voltage 1.5

# Set local variable
set my_var 10
```

### 3. Getting Values
Fetch a value from an instrument parameter.

**Syntax:**
```text
get <alias_or_path>
```

### 4. Running the Sequencer
Trigger the FPGA sequencer and capture returned data. The return value is a single data object (4xN array).

**Syntax:**
```text
run(<seq_name>, <seq_code>)
# OR
<data> = run(<seq_name>, <seq_code>)
```

**Example:**
```text
# Just run
run("seq1", "code.py")

# Run and capture data
d = run("seq1", "code.py")
```

### 5. Initializing Data
Define the structure of the data table before saving. This must be called before any `save()` command.

**Syntax:**
```text
init_data(param=(<axis1>, <axis2>, ...))
```

**Example:**
```text
init_data(param=("voltage", "current"))
```

### 6. Saving Data
Save data points to the experiment log/database. The arguments must match the initialized schema. `data` accepts a single data object (4xN array).

**Syntax:**
```text
save(params=(<val1>, <val2>, ...), data=<data_object>)
```

**Example:**
```text
save(params=(v, i), data=d)
```

### 7. Timing
Pause execution for a specified duration (in seconds).

**Syntax:**
```text
sleep(<seconds>)
```

**Example:**
```text
sleep(0.5)
```

### 8. Loops
Iterate over a range of values.

**Syntax:**
```text
for <variable> in (from <start> to <end> step <step_size>) {
    <statements>
}
```

**Example:**
```text
for v in (from 0 to 10 step 0.1) {
    set dac v
    sleep(0.01)
}
```

### 9. Conditional Logic
Execute statements based on a condition.

**Syntax:**
```text
if (<condition>) {
    <statements>
} else {
    <statements>
}
```

**Supported Comparisons:** `==`, `!=`, `<`, `<=`, `>`, `>=`

**Example:**
```text
if (v > 2.5) {
    set dac 0
} else {
    set dac v
}
```

## Example Script
```text
# Initialize data table schema
# param: tuple of axis names
init_data(param=("v", "i"))

# Define a threshold
param threshold = 3.0

for v in (from -5 to 5 step 0.5) {
    # Conditional logic
    if (v > threshold) {
        set dac threshold
    } else {
        set dac v
    }
    
    # Wait for settle
    sleep(0.1)
    
    # Get parameter value
    i = get device2.voltage
    
    # Run sequencer with arguments and capture data
    d = run("seq1", "some_code.py")
    
    # Save data matching the initialized schema
    # params: tuple of scalar values for axes
    # data: single data object (4xN array)
    save(params=(v, i), data=d)
}
```
