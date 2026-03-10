# Architecture Overview

The current architecture baseline is documented in detail in the Chinese design docs:

- [Detailed Architecture Design (Chinese)](./ARCHITECTURE.zh-CN.md)
- [Field Definitions (Chinese)](./FIELDS.zh-CN.md)

Current architecture baseline:

- `main_brain`: the only user-facing subject
- `executor`: the execution system called by the brain
- `reflection`: `light_insight` every turn, `deep_insight` on demand or by periodic signal
- explicit turn loop: `main_brain -> executor` instead of an outer workflow graph
- `session -> cognitive_event -> memory -> skills`: the data and growth path

The detailed document defines:

- design principles
- framework choices
- runtime flows
- interrupt, pause, and resume for the executor
- `CompositeBackend` routing for memory, state, and skills
- `cognitive_event` fields
- light insight and deep insight
- long-term memory layout
