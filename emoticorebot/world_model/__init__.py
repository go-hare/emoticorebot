"""Shared world-model runtime state."""

from emoticorebot.world_model.projectors import (
    artifact_refs_from_blocks,
    build_task_blueprint,
    merge_unique_strings,
    project_task_from_blueprint,
    project_task_from_executor_record,
)
from emoticorebot.world_model.reducers import (
    apply_executor_terminal,
    clear_current_task,
    set_current_task,
    touch_current_topic,
)
from emoticorebot.world_model.schema import (
    WorldCurrentCheck,
    WorldCheckRecord,
    WorldModel,
    WorldTask,
    normalize_current_checks,
    normalize_mainline,
    normalize_stage,
    normalize_string_list,
    utc_now,
)
from emoticorebot.world_model.store import WorldModelStore

__all__ = [
    "WorldCurrentCheck",
    "WorldCheckRecord",
    "WorldModel",
    "WorldModelStore",
    "WorldTask",
    "apply_executor_terminal",
    "artifact_refs_from_blocks",
    "build_task_blueprint",
    "clear_current_task",
    "merge_unique_strings",
    "project_task_from_blueprint",
    "normalize_current_checks",
    "normalize_mainline",
    "normalize_stage",
    "normalize_string_list",
    "project_task_from_executor_record",
    "set_current_task",
    "touch_current_topic",
    "utc_now",
]
