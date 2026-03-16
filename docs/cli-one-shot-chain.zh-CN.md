# CLI One-Shot 链路说明

本文只记录 `emoticorebot agent -m ...` 和 `emoticorebot cron run ...` 这条 one-shot 执行链路，方便后续继续整理。

## 范围

- 命令入口：
  - `emoticorebot agent -m ...`
  - `emoticorebot cron run <job_id>`
- 关键文件：
  - `emoticorebot/cli/commands.py`
  - `emoticorebot/bootstrap.py`
  - `emoticorebot/runtime/kernel.py`
  - `emoticorebot/delivery/service.py`

## 当前链路

### 1. one-shot 命令入口

`agent -m` 和 `cron run` 都会走相同模式：

1. 生成一个当前请求专属的 `pending_message_id`
2. 记录当前 session 已知的 `known_task_ids`
3. 并发启动两个协程
   - `_run_direct()`：调用 `RuntimeHost.process_direct(...)`
   - `_consume_outbound()`：持续消费 `bus.consume_outbound()`
4. 只处理 `reply_to == pending_message_id` 的 outbound 消息
5. 根据 `reply_kind`、`task_id`、流式状态、task 状态来决定何时 `completed.set()`

### 2. direct 入口

`RuntimeHost.process_direct(...)` 会：

1. 构造 `InboundMessage`
2. 调用 `ConversationGateway.process_direct(...)`
3. 进入 `RuntimeHost._process_message(...)`
4. 调用 `RuntimeKernel.handle_user_message(...)`
5. 等待首个非 stream-delta 的 approved/redacted reply

注意：

- `process_direct()` 只负责首轮 turn 的返回
- 如果主脑决定 `create_task`，真正的任务完成通知不会通过 `process_direct()` 返回，而是后续继续走 outbound 队列

### 3. outbound 收尾

`DeliveryService` 会把批准后的 reply 转成 `OutboundMessage`，核心字段在 `metadata`：

- `reply_kind`
  - `answer`
  - `ask_user`
  - `status`
- `task_id`
- `_stream`
- `_stream_id`
- `_stream_state`

one-shot 收尾逻辑大致是：

- stream delta：直接打印，不结束
- stream final：
  - 如果还没识别到任务，就尝试推断 `awaited_task_id`
  - 如果这是普通直答，没有任务，则可以直接结束
  - 如果这是任务前置状态回复，则继续等待任务结果
- non-stream final：
  - 如果是目标 task 的 `answer/ask_user`，结束
  - 如果 task 还没进入终态，继续等
  - task 已终态或没有任务时，结束

## 这次发现的真实问题

### 1. `agent -m` 会挂住

根因在 `emoticorebot/cli/commands.py` 的 `agent()` one-shot 路径：

- `_consume_outbound()` 内部会给 `awaited_task_id` 赋值
- 但函数里缺少 `nonlocal awaited_task_id`
- 一旦处理到第一条 stream final / final outbound，就会触发闭包局部变量错误
- 消费协程死掉后，`completed` 没机会被正常设置，于是 CLI 表现为：
  - 先打印一句“我来处理”
  - 文件实际创建成功
  - 但前台一直不退出

### 2. 任务完成通知会丢

旧逻辑里，如果前面已经有 stream 输出：

- 结束时只调用 `_finish_stream_output()`
- 不会再打印真正的任务完成结果

所以会出现：

- 用户已经看到首句
- worker 也执行完了
- 但 CLI 前台没有最终“已完成”说明

### 3. 消费协程异常不够显式

旧逻辑下：

- `_consume_outbound()` 出错后，主流程不一定立刻抛出这个异常
- 更容易表现成“挂起”
- 不利于定位问题

## 这次已经落地的修复

修改文件：`emoticorebot/cli/commands.py`

### `agent -m`

- 给 `_consume_outbound()` 补了 `nonlocal awaited_task_id`
- 增加 `consume_error` 收集
- 如果 outbound 消费协程出错，主流程会显式抛出
- 如果前面已经发生 stream，且最后等到了任务结果，会在 `_finish_stream_output()` 后继续打印最终结果

### `cron run`

- 同样补了 `consume_error`
- 同样保证消费协程异常能回抛

## 已验证结果

### 真实环境

环境：

- `conda` 环境名：`emoticorebot`

实测命令：

1. 任务请求

```bash
conda run --no-capture-output -n emoticorebot emoticorebot agent --session cli:realverify2 -m '在 .timing_probe/realverify2.py 创建一个 Python 文件，内容是 def add(a, b):
    return a + b' --no-markdown
```

结果：

- 约 43 秒完成
- 会先打印“好的，我这就帮你创建...”
- 任务结束后会继续打印最终“已完成”结果
- CLI 正常退出

2. 直接问答

```bash
conda run --no-capture-output -n emoticorebot emoticorebot agent --session cli:realverify4 -m '1 + 1 = ?' --no-markdown
```

结果：

- 约 8 秒完成
- 正常输出答案
- CLI 正常退出

### 测试

```bash
conda run --no-capture-output -n emoticorebot pytest -q
```

结果：

- `115 passed, 2 skipped`

另外补了一条专门测试：

- `tests/test_cli_commands.py::test_agent_one_shot_prints_task_result_after_stream`

它覆盖的场景是：

1. 先收到 stream delta
2. 再收到 stream final
3. 再收到 task final answer
4. 断言最终结果确实被打印

## 明天可以继续收的点

这次只是把链路修通，还没有把代码彻底收干净。

### 1. 抽掉 `agent -m` / `cron run` 的重复逻辑

目前两边都有一套 one-shot outbound 收尾逻辑，建议抽成一个小函数，例如：

- `run_one_shot_turn(...)`
- `collect_one_shot_reply(...)`

要求：

- 输入只保留 session、message、transport、host
- 输出统一成 `OneShotResult`

### 2. 明确 one-shot 状态模型

当前逻辑依赖多个变量配合：

- `streamed`
- `awaited_task_id`
- `final_response`
- `known_task_ids`
- `completed`

建议改成一个很小的状态对象，例如：

- `pending_message_id`
- `awaited_task_id`
- `saw_stream`
- `final_text`
- `done`

这样可读性会更高。

### 3. 明确“直答结束”和“任务结束”的判定

现在规则已经能跑通，但还可以再收：

- 无任务：首个 final answer / ask_user 即可结束
- 有任务：
  - `status` 不结束
  - 目标 task 的 `answer/ask_user` 才结束
  - 或者 task 已进入终态且已经拿到最终可展示文本时结束

如果后面还会做更多流式/多通道收尾，建议把这套规则变成纯函数。

### 4. 统一 stdout 行为

现在 one-shot 路径里：

- stream delta 直接写 stdout
- final result 走 `_print_agent_response()`

视觉上可以接受，但实现上还是分裂的。后面可以考虑把：

- stream 首句
- status
- final answer

统一到一个更明确的 CLI renderer。

## 结论

这条线目前已经“能工作”：

- 直答可退出
- 任务可退出
- 任务最终结果会显示
- 测试和真实环境都已过

但 `agent -m` 和 `cron run` 的 one-shot 收尾代码还有重复，建议后续做一次小范围整理，不要继续在两处并行长代码里堆条件。
