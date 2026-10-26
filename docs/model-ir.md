# Canonical model IR

The QpiAI Opt intermediate representation (IR) is a versioned JSON format for persistence, reproducibility, automation, and SDK adapters. It complements LP/MPS files and native QUBO/HUBO/Ising representations.

```python
from qpiai_opt.opt_modeller import dump, dumps, load, loads, to_ir, from_ir

document = to_ir(model)  # dict
payload = dumps(model, indent=2)
clone = loads(payload)

dump(model, "model.qpiai.json")
clone = load("model.qpiai.json")
```

## Public functions

| Function | Input | Return | Failure behavior |
|---|---|---|---|
| `to_ir(value)` | `Model`, `QUBOProblem`, `HUBOProblem`, `IsingProblem`, `MILPProblem`, `QubitHamiltonian` | canonical `dict` | `TypeError` for other objects |
| `from_ir(document)` | mapping | reconstructed native object | `TypeError` for non-mapping; `ValueError` for missing/unknown fields, version, references, duplicate terms, numbers, domains, or kinds; Pydantic validation for polynomial objects |
| `dumps(value, *, indent=None)` | supported native object | deterministically key-sorted JSON string | rejects NaN and unsupported values |
| `loads(payload)` | `str`, `bytes`, `bytearray` JSON | reconstructed object | `json.JSONDecodeError` or IR validation error; duplicate object keys and non-standard NaN/infinity tokens are rejected |
| `dump(value, path, *, indent=2)` | object and path | written `Path` | normal filesystem errors |
| `load(path)` | path | reconstructed object | normal filesystem or parse/validation errors |

The schema version is `qpiai-opt-ir/1.0`. `from_ir()` rejects other version values.

## Common envelope

Every document has:

| Field | Type | Meaning |
|---|---|---|
| `ir_version` | string constant | `qpiai-opt-ir/1.0` |
| `kind` | enum | `model`, `qubo`, `hubo`, `ising`, or `qubit_hamiltonian` |

`kind="model"` documents carry `transformation_metadata`, an array of caller- or compiler-recorded transformation descriptions. Each item must be a JSON object. Native QUBO/HUBO/Ising documents contain the fields listed under their respective kinds below.

All numbers are JSON numbers except unbounded variable limits, represented by the exact strings `"+inf"` and `"-inf"`. NaN is never valid.

## Numeric representation

Numeric fields use IEEE-754 binary64 values. JSON numbers are converted to that representation when the document is read, so ordinary decimal rounding is expected and the IR is not an arbitrary-precision decimal format. Values outside the finite binary64 range are outside the supported input domain; scale coefficients, bounds, and offsets before serializing them. A nonzero value that is too small for binary64 can underflow to zero, and a value that is too large can overflow, so neither retains its mathematical value in the IR.

Exact integer meaning is guaranteed only through `2**53`. Larger integer values may round during binary64 conversion and must not be used where every integer digit matters. The SDK accepts finite Python and NumPy real values at its numeric boundaries; `decimal.Decimal` is not an accepted input type. Use a supported `int`, `float`, NumPy real scalar, or `fractions.Fraction` where the API accepts it, and scale values that approach the binary64 limits.

## `kind="model"`

Required fields are `name`, `sense`, `variables`, `objective`, `constraints`, `parameters`, `parameter_bindings`, and `transformation_metadata`.

### Variables

Each variable has a unique `name` and `domain`. Domains are:

| Domain | Additional fields |
|---|---|
| `BINARY`, `SPIN` | none |
| `INTEGER`, `CONTINUOUS`, `SEMI_CONTINUOUS`, `SEMI_INTEGER` | `lower_bound`, `upper_bound` |
| `FRACTIONAL` | bounds and `precision_decimals` |
| `MIXED` | `valid_discrete_values`, two-element `continuous_range` |

Names are semantic IDs in IR. Expressions refer to them and deserialization rejects unknown or duplicate references.

### Expressions

```json
{
  "constant": 3,
  "terms": [
    {"coefficient": 2, "variables": ["x"]},
    {"coefficient": -1.5, "variables": ["x", "y"]}
  ]
}
```

Repeated names preserve powers/products exactly. `sense` is `MIN` or `MAX`.

### Constraints

| `kind` | Fields |
|---|---|
| `equal`, `less_equal`, `greater_equal` | `lhs`, `rhs` expressions |
| `indicator` | `binary`, `active_value`, nested `relation` |
| `and`, `or`, `xor`, `not` | ordered variable-name array |
| `custom_penalty` | `expression`, `penalty_label` (string or null) |
| `sos1`, `sos2` | `name`, variable names, numeric `weights` |

Every constraint carries `label` and `penalty_weight`; either value may be null. A null penalty weight retains automatic penalty selection. All listed fields are required, including nullable fields. Logical arrays contain three names for `and`/`or`/`xor`, or two for `not`, in constructor argument order. Indicator `relation` contains only its relational `kind`, `lhs`, and `rhs`.

### Parameters

`parameters` is an array of `{name, value}`. `parameter_bindings` contains objects with:

- `parameter`;
- `target`: `objective_coefficient`, `constraint_rhs`, `lower_bound`, or `upper_bound`;
- `variable` for coefficient/bound targets, or zero-based `constraint` for an RHS target.

Deserialization restores the serialized objective, bounds, and constraints, then validates their parameter bindings without reapplying parameter values. Use `model.set_parameter(name, value)` to update all registered targets atomically. Duplicate bindings are rejected.

## Polynomial kinds

### QUBO

Fields: `num_variables`, `terms[{indices:[i,j], coefficient}]`, `matrix_format`, `offset`, `sense`, and `variable_names` (an empty array when names are absent). The reconstructed `QUBOProblem` revalidates index range, symmetry, finite values, and name count.

### HUBO

Fields: `num_variables`, `terms[{variables:[label,...], coefficient}]`, `sense`, and `variable_names` (an empty array when no explicit order was supplied). Solver-facing HUBO labels are non-empty strings; an empty term's `variables` array represents the constant. Repeated labels within a term are rejected. Labels are sorted within each term, and a second term with the same labels in another order is rejected as a duplicate. The declared variable order retains isolated variables and is revalidated by `HUBOProblem`.

### Ising

Fields: `num_variables`, `linear` (stringified integer index keys), `quadratic[{indices:[i,j], coefficient}]`, `offset`, `sense`, and `variable_names`. Linear keys use canonical decimal text: `"0"` and `"12"` are valid, while leading-zero, signed, and out-of-range forms are rejected. `num_variables` is a non-negative integer. `variable_names` may be an empty array when names were not supplied, including for the valid zero-spin constant case.

## Round-trip guarantees and limits

The guarantee is mathematical equivalence within the supported public model semantics: domains, bounds, sense, objective, constraints, offsets, labels, parameters, and transformation metadata survive. Runtime client state and solver results are not part of the model representation. Private source-model snapshots on compiled polynomial problem objects are also excluded: their IR preserves the compiled energy and declared labels, but not the original model's domain-decoding or source-objective metadata. Serialize the source `Model` separately when that interpretation is needed after loading.

JSON object ordering is not semantic. `dumps()` sorts keys for reproducible text; variable, term, and constraint arrays preserve defined order.

Readers reject unknown fields in the defined schemas. Generic transformation-metadata objects retain caller-defined keys; the piecewise-linear record has a strict schema and consistency checks.

Collection members are validated before lookup or duplicate detection. Malformed variable names, indices, logical arity, SOS arrays, and canonical-key collisions therefore fail at the affected IR path instead of being aggregated or surfacing a generic Python container error.

## Hamiltonians and linear problem containers

`MILPProblem` serializes its underlying mathematical model using `kind="model"`; solver configuration and file paths are execution inputs and are not part of that model document. A path-backed container is read through the existing LP/MPS reader before conversion.

`kind="qubit_hamiltonian"` stores `num_qubits`, `sense`, `variable_names`, and `terms`. Each term contains a finite real `coefficient` and an `operators` array of `[qubit_index, "I" | "X" | "Y" | "Z"]` pairs; an empty array is the identity. This IR preserves arbitrary real Pauli terms. Conversion to QUBO or Ising is narrower and requires only identity, Z, and ZZ terms. Reading or writing a Hamiltonian requires the optional gate-based optimization dependency.
