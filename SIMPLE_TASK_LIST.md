# 简单任务列表

## 改动

1. **TaskUnit 加 `title` 字段**
```python
@dataclass
class TaskUnit:
    task_id: str
    title: str = ""  # 新增
    # ... 其他不变
```

2. **SessionTaskSystem 用 dict 存任务**
```python
class SessionTaskSystem:
    def __init__(self, ...):
        self._tasks: dict[str, TaskUnit] = {}  # 任务列表
        # current/prev 保留（向后兼容）
```

3. **加两个方法**
```python
def find_task_by_title(self, title: str) -> TaskUnit | None:
    """按名称找任务"""
    for task in self._tasks.values():
        if task.title == title:
            return task
    return None

async def supplement_task(self, task_id: str, request: str) -> bool:
    """补充任务"""
    task = self.get_task(task_id)
    if task and task.status == "done":
        task.status = "running"
        task.params["supplement_requests"] = request
        await self._start_task(task, worker)
        return True
    return False
```

## 使用

创建任务时传 `title`，补充任务时用 `find_task_by_title` 找任务。

完。
