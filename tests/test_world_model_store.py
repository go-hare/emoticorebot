from __future__ import annotations

import json

from emoticorebot.world_model.schema import WorldCheckRecord, WorldModel, WorldTask
from emoticorebot.world_model.store import WorldModelStore


def test_world_model_store_persists_v1_schema(tmp_path) -> None:
    store = WorldModelStore(tmp_path)
    model = WorldModel(
        session_id="cli:direct",
        current_topic="修复 reflection",
        current_task=WorldTask(
            task_id="task_1",
            goal="修复 reflection",
            status="running",
            summary="已定位问题",
            mainline=["看问题", ["改代码", "补测试"], "执行测试"],
            current_stage=["改代码", "补测试"],
            current_checks=["修改 reflection/manager.py", "补 governor 测试"],
            last_result="已定位问题",
            check_history=[
                WorldCheckRecord(
                    check="读取报错",
                    result="已定位到 reflection/manager.py",
                    artifacts=["emoticorebot/reflection/manager.py"],
                )
            ],
            artifacts=["emoticorebot/reflection/manager.py", "tests/test_reflection_governor.py"],
        ),
    )

    store.save(model)
    loaded = store.load("cli:direct")
    payload = json.loads(store.path_for("cli:direct").read_text(encoding="utf-8"))

    assert loaded.schema_version == "world_model.single_task.v1"
    assert loaded.current_task is not None
    assert loaded.current_task.task_id == "task_1"
    assert loaded.current_task.goal == "修复 reflection"
    assert [item.title for item in loaded.current_task.current_checks] == ["修改 reflection/manager.py", "补 governor 测试"]
    assert [item.status for item in loaded.current_task.current_checks] == ["pending", "pending"]
    assert payload["schema_version"] == "world_model.single_task.v1"
    assert "current_task_id" not in payload
    assert "tasks" not in payload


def test_world_model_store_clear_removes_file(tmp_path) -> None:
    store = WorldModelStore(tmp_path)
    store.save(WorldModel(session_id="cli:direct", current_topic="hello"))

    path = store.path_for("cli:direct")
    assert path.exists()

    store.clear("cli:direct")

    assert not path.exists()
