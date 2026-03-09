# Architecture Overview

The current architecture baseline is documented in detail in the Chinese design doc:

- [Detailed Architecture Design (Chinese)](./ARCHITECTURE.zh-CN.md)

This project is moving from the older `EQ / IQ` naming toward a clearer model:

- `main_brain`: the only user-facing subject
- `executor`: the execution system called by the brain
- `reflection`: light and deep insight loops
- `session -> cognitive_event -> memory`: the three-layer data flow

The detailed document defines:

- design principles
- framework choices
- runtime flows
- interrupt, pause, and resume for the executor
- `CompositeBackend` routing for memory, state, and skills
- `cognitive_event` fields
- light insight and deep insight
- long-term memory layout
