# Modeller limits and capacity

The modeller has no single maximum variable or constraint count. Available memory and the chosen conversion or solver determine practical capacity. The hard boundaries below apply before those downstream limits.

## Construction and compilation boundaries

| Operation | Boundary | Consequence |
| --- | --- | --- |
| Canonical polynomial term | At most 100,000 retained factors. | Repeated binary factors collapse and repeated spin factors cancel in pairs; distinct non-binary factors still count. |
| Expression multiplication | At most 100,000 projected intermediate term/factor operations. | Construct a known sparse high-degree polynomial from `Term` objects instead of expanding a dense product. |
| Source HUBO | No separate degree ceiling below the factor guard. | Local degree contributes to deterministic solver-work limits; platform limits are independent. |
| LP/MILP export | Linear objective and relational bodies. | Equality indicators become two rows; SOS1/SOS2 use native export or exact MILP reformulation. |
| Continuous QP export | Continuous variables, quadratic objective, linear constraints. | Matrix export verifies convex MIN or concave MAX curvature. |
| `Fraction` | `precision_decimals` from 0 through 15; scaled units at most `2**53 - 1` in magnitude. | Bounds must lie exactly on the decimal grid. |
| `encode_continuous()` | 1–53 bits; direct default 8 bits; compilation default cap 20 bits. | Bounds must be finite. Supply either a bit count or decimal precision. |
| `encode_integer_binary()` | Finite integral bounds within the exact binary64 integer range. | Generated-bit count follows the interval width. |
| Piecewise interpolation | Equal finite breakpoint/value sequences, at least two points, strictly increasing breakpoints. | Adds one weight per point, three linking rows, and one SOS2 set. |
| SOS membership | At least one distinct variable; optional weights are finite, unique, and count-matched. | SOS1 permits one nonzero member; SOS2 permits two adjacent members. |
| Canonical IR | Schema version 1.0; no modeller byte ceiling. | Transport and storage systems can impose narrower limits. |

Compiled size can exceed source size. Constraint slack, domain encodings, logical auxiliaries, and HUBO quadratization all add variables or terms. Capacity estimates should record both sizes.

## Plan model capacity

Record source variables, constraints, polynomial degree, and nonzero terms separately from exported columns, rows, and nonzeros. Domain encoding and constraint reformulation can substantially increase the representation passed to a solver.

Use `model.variables()` and `model.constraints` to enumerate model variables and stored constraints; generated modelling variables can already be present. `inspect(model)` reports the classified representation and work metrics; a target-specific export establishes its compiled size. Validation and inspection do not solve a model. See [inspection operations](../automation.md#inspection-operations) and [transformation records](transformations.md#transformation-records).

For a linear matrix with `m` rows and `n` columns, dense binary64 coefficients require `8*m*n` bytes before array and model metadata. A 20,000-by-10,000 dense matrix alone requires 1.6 GB. CSR storage instead grows with nonzero coefficients, index arrays, and row pointers. Keep `sparse_matrix=True` unless the receiving API requires dense data.

For QP, `to_qp_matrix()` additionally builds the Hessian and verifies source-sense curvature. A dense `n`-by-`n` Hessian requires `8*n*n` bytes before overhead. A small source expression does not imply a cheap curvature check or solve.

## Construct large expressions

Use `quicksum()` or `dot()` for a known collection of terms. Repeated addition and one-at-a-time objective coefficient replacement rebuild expressions and can add substantial construction cost. Register variables explicitly when they must remain in the model despite zero coefficients.

Represent an already known sparse monomial with `Term(coefficient, factors)` instead of expanding a dense symbolic product. The factor guard still applies. Polynomial degree, retained term count, and projected multiplication work are different quantities; a high-degree sparse expression can be representable even when a dense expansion is rejected.

Model construction, compilation, export, authentication, and solving are separate operations. Measure each operation needed by the application, including the effect of generated variables and rows. A construction measurement alone does not establish end-to-end solver latency or the cost of another target representation.

## Encoding and reformulation growth

- Binary variables require no domain bits. Integer and fractional grids require bounded binary weights determined by the declared interval. Continuous domains require a selected resolution during model compilation; tighter resolution increases the compiled variable count.
- Inequality penalties can add binary slack. Logical XOR adds one binary auxiliary. Polynomial quadratization can introduce shared product auxiliaries; its size depends on term structure and shared factors.
- Indicator equalities export two bounded linear rows. SOS1/SOS2 selectors and piecewise interpolation add linear rows and auxiliary columns when reformulated. Native SOS export requires the receiving solver to enforce the separate SOS records.
- Symmetric QUBO output stores two half-coefficient entries for each off-diagonal interaction; dictionary entry count therefore differs from mathematical interaction count.

Native problem objects produced by `from_model()` retain independent source snapshots. Create a new problem after source edits when the next operation should use the updated model. Conversion records have their own [retention and decoding contracts](api-modeller.md#function-hubo_to_qubo); in particular, `MILPToQUBOConvert` retains the supplied model. Keep the corresponding problem or conversion record until decoding and source feasibility checks are complete.

## Execution capacity

Modeller capacity is separate from the authenticated local solver's deterministic resource limits. Local polynomial limits also account for interaction degree and estimated enumeration or annealing work. Platform capacity and available methods are separate service contracts.

Inspect the current [local structural limits](local-solving.md#structural-limits) and `provider_capabilities()` before execution. If a model exceeds the selected route, reduce it without changing the intended problem, choose a compatible external representation, or select platform execution when authorized. Conversion, validation, and inspection remain credential-free.
