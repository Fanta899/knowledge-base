# XX EM (Event Machine) 框架笔记

## 1. 整体架构

EM 是 XX 自研的**用户态事件驱动调度框架**，L2 代码构建在其上。核心设计：L2 代码不做任何轮询或阻塞等待，而是被 EM 调度器回调。

### 三大实体

| 实体 | 类 | 作用 |
|---|---|---|
| **Queue Group** | `EmQueueGroup` | 绑定一组 CPU core，决定事件在哪些核上执行 |
| **Queue** | `EmQueue` | 事件队列，属于某个 Queue Group，有优先级和类型 |
| **EO (Execution Object)** | `EoBase` | 逻辑处理单元，拥有多个 Queue，注册回调函数 |

关系：**Core → QueueGroup → Queue → EO**

---

## 2. 消息分发流程

### EO 创建时注册 receiveCallback

`EoHandlerL2Nrt::allocateEo()` 中调用：
```cpp
platform::EmIf::emEoCreate(name, startCallback, nullptr, stopCallback, nullptr, receiveCallback, eo);
```
将 `receiveCallback` 作为函数指针注册到 EM 框架。

### EO 启动流程

```
EoBase::runEm()
  → em_queue_enable_all(eoId)     // 启用所有 queue
  → fillNotifs()                  // 构造 EoInitInd 通知
  → em_eo_start(eoId, notifs)    // EM 在绑定 core 上调用 startCallback
```

### 事件分发的两条路径

**路径 A：FSM 路由模式（L2-PS/L2-HI）**
```
EM 调度器 → receiveCallback() → processEvent()
  → processSpecialEvents()           // 检查 init/radparam 等特殊事件
  → EmFsmBase* fsm = (EmFsmBase*)q_ctx  // 从 queue_context 取 FSM
  → fsm->processEvent(EmFsmEvent)    // QueueStateBase::eventCallback()
  → fsm->deleteEvent() / processDelayedEvents()
```

**路径 B：直接 receive 模式（L2-LO）**
```
EM 调度器 → receiveCallback()
  → eo->receive(event, type, msgId, queue_context)
    → handleEvent()
      → switch(EM_EVENT_TYPE(type))
          SW/TIMER  → DlSwEventHandler
          SYSCOM    → DlSyscomMsgHandler
          VECTOR    → DlSwEventHandler::handleEventBundle
    → em_free(event)
```

### Queue 类型

| 类型 | 值 | 行为 |
|---|---|---|
| `EM_QUEUE_TYPE_ATOMIC` | 1 | 同一时刻只有一个 core 处理 |
| `EM_QUEUE_TYPE_PARALLEL` | 2 | 多核可并行处理 |
| `EM_QUEUE_TYPE_UNSCHEDULED` | 4 | 手动出队，不由 EM 调度 |

---

## 3. EM 与 epoll 的根本区别

| 维度 | epoll | XX EM |
|---|---|---|
| 场景 | 通用 Linux I/O 多路复用 | 实时通信专用事件调度 |
| 触发源 | 文件描述符 (socket/pipe/fd) | 内存中的事件对象 (em_event_t) |
| 调度方式 | 内核态 → 用户态唤醒 | **纯用户态**调度，无系统调用 |
| 核心机制 | `epoll_wait()` 阻塞等内核通知 | 无锁队列 + busy-polling |
| 延迟 | 微秒级（内核上下文切换） | 纳秒级（全在用户态） |

**EM 与 epoll 没有任何关系。**

---

## 4. Kernel Bypass：EM 如何绕开内核 I/O

### 4.1 内存层：绕开 malloc / 内核页管理

EM 的所有事件和数据分配在**预留的 Hugepage 内存**上，不走标准 malloc：

- **x86 (VDU/VCU)**：DPDK 的 `rte_malloc`、`rte_mempool`（底层是 Linux hugepage mmap，初始化后全在用户态）
- **ARM (BBP)**：ODP 共享内存 + XX `AaMem` 分配器
- **EM Pool**：`em_pool_create()` 初始化时预分配子池，运行时 em_alloc / em_free 只是移动池中的指针，无系统调用

```
初始化（仅一次系统调用）:
  mmap hugepages → 切割为 em_pool 子池

运行时（零系统调用）:
  em_alloc() → 池中取 buffer（无锁 CAS）
  em_free()  → 归还到池中
```

### 4.2 队列层：绕开 pipe / socket / 内核 IPC

进程/线程间传递事件不经过内核：

- **x86**：DPDK `rte_ring` — 无锁环形队列（CAS 原子操作 + hugepage 共享内存）
  - `rte_ring_mp_enqueue`（多生产者入队）
  - `rte_ring_mc_dequeue`（多消费者出队）
- **ARM**：ODP queue 原语（共享内存 + 原子操作）

```
em_send(event, queue) 本质：
  → rte_ring_mp_enqueue(queue->ring, event_ptr)  // 一条 CAS 指令
```

### 4.3 调度层：绕开 Linux 调度器

EM 的每个 core 运行一个独占的用户态事件循环，不被 Linux 调度器抢占：

- **Core 隔离**：Linux `isolcpus` 内核参数将 L2 核心从 Linux 调度器移除
- **Core Mask**：`em_core_mask_set()` + `em_queue_group_create()` 绑定 queue group 到特定核
- **Busy-poll 循环**：每个核运行独占事件循环

```
// EM 调度器伪代码（core N）
while (running) {
    for (queue : queues_on_this_core) {
        event = rte_ring_mc_dequeue(queue->ring);  // 无锁出队
        if (event) {
            eo->receiveCallback(eo_ctx, event, ...);  // 直接函数调用
        }
    }
    // 无 sleep、无 epoll_wait、无上下文切换
}
```

### 4.4 网络 I/O 层：绕开内核协议栈

与 L1/fronthaul 的通信也不走内核网络栈：

- **DPDK PMD (Poll Mode Driver)**：用户态网卡驱动，DMA 直接写入 hugepage `rte_mbuf`
- rte_eth_rx_burst() 直接从网卡 ring buffer 取包，无中断、无系统调用
- L1 硬件加速器通过 `EM_IF_ID_0` / `EM_IF_ID_1` 直接注入 EM queue

### 4.5 平台后端总结

| 平台 | 硬件 | 后端技术 |
|---|---|---|
| BBP (ARM) | 基站 baseband | ODP + XX 自研 EM |
| VDU/VCU (x86) | 虚拟化 DU | DPDK + RCP-ODP |
| SNF (Marlin/Nemo) | XX 专用硬件 | XX 硬件加速 EM |

配置切换通过编译宏：`USE_EM_ODP`、`RCP_ODP`、`PLTF_BBP`、`VDU`、`VCU`、`PLATFORM_DEVICE_MARLIN` 等。

### 4.6 对比总结

```
传统 Linux (epoll):
  App → write() → 内核 → 协议栈 → NIC driver → 硬件
  硬件 → 中断 → 内核 → 协议栈 → epoll_wait() 唤醒 → App
  每次 I/O 至少 2 次用户态/内核态切换

XX EM (kernel bypass):
  App → em_send() → rte_ring 入队 (用户态 CAS)
  EM dispatcher busy-poll → rte_ring 出队 → receiveCallback()
  全程零系统调用、零上下文切换
```

**核心取舍**：牺牲 CPU 占用率（busy-polling 空转），换取**确定性亚微秒延迟**——这是电信级实时系统的标准做法。

---

## 5. 架构总图

```
┌──────────────────────────────────────────────────────────┐
│                  EM Scheduler (per core)                  │
│  busy-poll 事件循环：检测 queue → 调用 receiveCallback    │
└─────────┬──────────────────┬──────────────────┬──────────┘
          │                  │                  │
    ┌─────▼─────┐     ┌─────▼─────┐     ┌──────▼─────┐
    │ QueueGrp  │     │ QueueGrp  │     │ QueueGrp   │  ← em_core_mask 绑核
    │ (core 0)  │     │ (core 1)  │     │ (core 0,1) │
    └─────┬─────┘     └─────┬─────┘     └──────┬─────┘
          │                  │                  │
    ┌─────▼─────┐     ┌─────▼─────┐     ┌──────▼─────┐
    │  Queue    │     │  Queue    │     │  Queue     │  ← 类型/优先级/context
    │ (atomic)  │     │(parallel) │     │ (atomic)   │
    └─────┬─────┘     └─────┬─────┘     └──────┬─────┘
          │                  │                  │
    ┌─────▼──────────────────▼──────────────────▼──────┐
    │                EO (EoBase)                        │
    │  receive(event, type, msgId, q_ctx)               │
    │    ├─ 路径A: FSM → QueueStateBase                 │
    │    └─ 路径B: switch(type) 直接分发                │
    └──────────────────────────────────────────────────┘

底层（Kernel Bypass）:
  ┌───────────────────────────────────────────────┐
  │  Hugepage Memory (mmap 一次，之后纯用户态)     │
  │  ├─ em_pool (事件池：em_alloc/em_free)         │
  │  ├─ rte_ring (无锁队列：em_send)              │
  │  └─ rte_mempool (DPDK 对象池)                 │
  └───────────────────────────────────────────────┘
```
