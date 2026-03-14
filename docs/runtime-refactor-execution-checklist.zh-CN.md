# EmotiCoreBot Runtime 重构执行清单

适用分支：`v3-max`

这份清单记录的是当前新架构已经完成的施工项，以及后续只剩可选增强项，不再包含迁移期双轨步骤。

## 1. 主线验收

- [x] 入站消息经由 `ConversationGateway` 进入系统
- [x] `ThreadStore / HistoryStore` 负责历史读写
- [x] `CompanionBrain` 负责用户回合决策
- [x] `SessionRuntime` 负责 session 级 live execution state
- [x] `CentralExecutor` 负责复杂任务执行
- [x] `TaskEventLoop` 负责消费 runtime event
- [x] `EventNarrator` 负责把事件转成陪伴式叙述
- [x] `OutboundDispatcher` 负责对外发送消息
- [x] `ReflectionCoordinator` 在首响后异步消费规范化输入

## 2. 架构边界

- [x] `brain` 不再持有任务表
- [x] `brain` 不再直接消费底层执行字典
- [x] `runtime` 不再依赖裸 `dict event` 作为核心协议
- [x] `execution` 不再挂在旧任务系统目录下
- [x] `session` 层不再持有 live runtime handle
- [x] 旧执行壳目录不再被生产代码引用

## 3. Runtime Kernel

- [x] `SessionRuntime` 已成为单 session live runtime 内核
- [x] `RunningTask` 与 `RuntimeTaskState` 已分离
- [x] input gate 已独立为 runtime 控制策略
- [x] waiting / blocked 的输入门控逻辑已收敛在 runtime
- [x] runtime 事件改为 typed protocol

## 4. Execution Layer

- [x] `CentralExecutor` 已作为执行层入口
- [x] Deep Agents backend 接线已迁到 `execution/backend.py`
- [x] trace / stream 逻辑已迁到 `execution/stream.py`
- [x] 技能加载逻辑已迁到 `execution/skills.py`
- [x] executor 通过 runtime callback 上报阶段与结果

## 5. Brain Layer

- [x] 用户回合决策与事件叙述已经拆分
- [x] `CompanionBrain` 仍保有高层决策权
- [x] 主脑文案已统一到 `SessionRuntime` 语义
- [x] 主脑没有退化成薄路由

## 6. Persistence 与 Reflection

- [x] `ThreadStore` 与 `HistoryStore` 已替代旧会话管理器
- [x] reflection 已消费规范化 `ReflectionInput`
- [x] 任务摘要与内部历史能进入反思链路
- [x] 长期记忆与运行时状态保持分层

## 7. 清理项

- [x] 旧 brain/runtime 代码壳已删除
- [x] 旧 central 执行壳已删除
- [x] 旧 README 架构描述已重写
- [x] 重构文档已改为终态视角
- [x] 生成缓存目录进入清理范围

## 8. 回归验证

- [x] `tests/test_architecture_boundaries.py`
- [x] `tests/test_runtime_state_machine.py`
- [x] `tests/test_thread_store.py`
- [x] 关键模块语法编译检查

## 9. 剩余可选增强

这些不属于“迁移未完成”，只属于后续可继续打磨：

- [ ] 增加 executor 集成测试
- [ ] 增加 replay / recovery 更细粒度测试
- [ ] 增强 runtime 可观测性与 trace 检索能力
- [ ] 丰富 narrator 的事件筛选策略
- [ ] 增强隐式续聊恢复能力

## 10. 一句话验收标准

如果还可以稳定地用一句话描述系统，说明当前重构已经真正收口：

`CompanionBrain 负责决策，SessionRuntime 负责执行，ThreadStore 负责记忆。`
