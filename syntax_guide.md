# QuAIL Experiment Syntax Guide

This document describes the custom syntax used for writing experiment scripts in the QuAIL system.

## Overview

The QuAIL syntax is a simple, imperative language designed for controlling physics experiments. It supports parameter management, sequencer execution, data saving, and basic control flow (loops).

## Commands

### 1. Parameter Definition
Define a variable or an alias for a QCoDeS instrument parameter.

**Syntax:**
```text
param <name> = <value_or_expression>
```

**Example:**
```text
# Define an alias (string)
param dac = "keithley.voltage"

# Define a variable from a GET command
param t = get dac
```

### 2. Setting Values
Set a value to a parameter alias or a local variable.

**Syntax:**
```text
set <alias_or_variable> <value>
```

**Example:**
```text
set dac 1.5
```

### 3. Getting Values
Fetch a value from an instrument parameter.

**Syntax:**
```text
get <alias_or_path>
```

### 4. Running the Sequencer
Trigger the FPGA sequencer and capture returned data vectors.

**Syntax:**
```text
run()
# OR
<var1>, <var2> = run()
```

**Example:**
```text
# Just run
run()

# Run and capture 2 data vectors
d1, d2 = run()
```

### 5. Saving Data
Save data points to the experiment log/database. Supports grouping via tuples.

**Syntax:**
```text
save(<key>=<value>, ...)
```

**Example:**
```text
save(var=(i, t), data=(d1, d2))
```

### 6. Timing
Pause execution for a specified duration (in seconds).

**Syntax:**
```text
sleep(<seconds>)
```

**Example:**
```text
sleep(0.5)
```

### 6. Loops
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
    run_sequencer()
    save(voltage=v)
}
```

## Example Script

```text
# Define parameters
param dac = "instr1.voltage"
param magnet = "instr2.field"

# Initialize
set magnet 0

# Sweep voltage
for v in (from -5 to 5 step 0.5) {
    set dac v
    
    # Wait for settle
    sleep(0.1)
    
    # Run the fast sequence
    run_sequencer()
    
    # Save the setpoint
    save(setpoint=v)
}
```
