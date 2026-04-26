# C++ 实现固定大小的 SimpleRingBuffer

## 概述

`FixedSizeSimpleRingBuffer` 是一个**固定大小、支持三态槽位**的环形缓冲区模板类，适用于生产者-消费者场景（例如日志、消息传递、实时数据流）。

其核心特点：

- **三阶段槽位状态机**：每个槽位经历 `free → reserved → readyToPop → free` 的循环
- **读写分离**：读（read）与弹出（pop）是两个独立步骤，允许消费者先读取数据、再显式释放槽位
- **原子操作**：写索引和弹出索引使用原子变量，支持并发访问；读索引为普通变量（单读者设计）
- **外部存储**：底层数组由子类持有并通过受保护构造函数注入，本类只保存引用

---

## 槽位状态机

每个槽位（`ArrayItem`）持有一个原子状态字节，状态转换如下：

```
        push()                advanceReadIndex()           pop()
free(0) ──────→ reserved(1)  ────────────────→ readyToPop(2) ──────→ free(0)
  ↑                                                                      │
  └──────────────────────────────────────────────────────────────────────┘
                          （状态值溢出后 CAS 归零）
```

`nextState()` 通过 `fetch_add(1)` 原子地推进状态，并在从 `readyToPop` 溢出时用 CAS 将状态归零（回到 `free`）。

---

## 三个索引的职责

| 索引 | 类型 | 职责 |
|---|---|---|
| `nextWriteIndex` | `Atomic<uint32_t>` | 下一个可写入的槽位位置 |
| `nextReadIndex`  | `uint32_t`         | 下一个可读取的槽位位置（单读者） |
| `nextPopIndex`   | `Atomic<uint32_t>` | 下一个可弹出/释放的槽位位置 |

---

## 典型使用流程

```
生产者：push(value)
消费者：readCurrentElement(value)  → 读取数据（不改变槽位所有权）
消费者：advanceReadIndex()         → 将槽位标记为 readyToPop
消费者：pop(value)                 → 取出数据并释放槽位（归还给生产者）
```

> 注意：`pop()` 自带读取功能，可以跳过 `readCurrentElement` + `advanceReadIndex` 直接一步完成读取和释放。

---

## 代码实现

```cpp
// ValueType: 存储元素的类型
// arraySize: 编译期确定的底层数组容量上限
template <typename ValueType, uint32_t arraySize>
class FixedSizeSimpleRingBuffer
{
public:
    // 禁用默认构造，强制子类通过受保护构造函数传入存储数组
    FixedSizeSimpleRingBuffer() = delete;

    // 将一个元素写入缓冲区
    // 如果缓冲区已满，或下一个槽位状态不为 free，则直接丢弃（不阻塞）
    void push(const ValueType& value)
    {
        if (isFull() or (ringBuffer.at(nextWriteIndex).enumState.load() != ArrayItem::ItemState::free))
        {
            return;
        }

        // 原子地占用写索引，获取当前写位置
        const uint32_t previousWriteIndex = incWriteIndex();
        auto& bufferItem = ringBuffer.at(previousWriteIndex);
        bufferItem.item = value;
        // 推进状态：free(0) → reserved(1)，表示数据已写入，等待消费者读取
        bufferItem.nextState(bufferItem.enumState);
    }

    // 读取当前读索引处的数据，但不推进读索引（不改变槽位状态）
    // 返回 false 表示没有可读元素
    [[nodiscard]] bool readCurrentElement(ValueType& value)
    {
        if (not itemsToRead())
        {
            return false;
        }

        value = ringBuffer.at(nextReadIndex).item;
        return true;
    }

    // 推进读索引，将当前槽位状态从 reserved(1) → readyToPop(2)
    // 需在 readCurrentElement() 之后调用，表示"已读完，可以弹出"
    [[nodiscard]] bool advanceReadIndex()
    {
        if (not itemsToRead())
        {
            printf("Trying to read but readCount=0");
            return false;
        }
        const uint32_t previousReadIndex = incReadIndex();
        auto& bufferItem = ringBuffer.at(previousReadIndex);
        // 推进状态：reserved(1) → readyToPop(2)
        bufferItem.nextState(bufferItem.enumState);
        return true;
    }

    // 从 readyToPop 槽位取出数据并释放槽位（状态回到 free）
    // 可独立使用，也可在 readCurrentElement + advanceReadIndex 之后调用
    [[nodiscard]] bool pop(ValueType& value)
    {
        if (isEmpty() or (ringBuffer.at(nextPopIndex).enumState.load() != ArrayItem::ItemState::readyToPop))
        {
            return false;
        }

        // 原子地占用弹出索引
        const uint32_t previousPopIndex = incPopIndex();
        auto& bufferItem = ringBuffer.at(previousPopIndex);
        value = bufferItem.item;
        // 推进状态：readyToPop(2) → free(0)（通过溢出+CAS归零实现）
        bufferItem.nextState(bufferItem.enumState);

        return true;
    }

    // 重置缓冲区，清空所有槽位和索引
    void clear() { initializeBuffer(); }

    // 返回已写入但尚未被 pop 的元素数量（write 到 pop 之间的元素数）
    [[nodiscard]] uint32_t getNumberOfElements()
    {
        if (isFull())
        {
            return maxSize;
        }
        if (isEmpty())
        {
            return 0;
        }
        // 处理环形回绕：若写索引已绕回到弹出索引之前，需加上 maxSize
        return (nextWriteIndex < nextPopIndex) ? ((nextWriteIndex + maxSize) - nextPopIndex)
                                               : (nextWriteIndex - nextPopIndex);
    }

    // 返回已写入但尚未被 read 的元素数量（write 到 read 之间的元素数）
    [[nodiscard]] uint32_t getNumberOfElementsToRead()
    {
        if (areAllElementsToRead())
        {
            return maxSize;
        }
        if (isNothingToRead())
        {
            return 0;
        }
        return (nextWriteIndex < nextReadIndex) ? ((nextWriteIndex + maxSize) - nextReadIndex)
                                                : (nextWriteIndex - nextReadIndex);
    }

    // 缓冲区为空：写索引与弹出索引重合，且该槽位状态为 free
    [[nodiscard]] bool isEmpty() const
    {
        return (nextWriteIndex == nextPopIndex) and
            (ringBuffer.at(nextPopIndex).enumState.load() == ArrayItem::ItemState::free);
    }

protected:
    // 每个槽位的数据结构
    struct ArrayItem
    {
        // 槽位的三种状态（状态值连续，便于 fetch_add 推进）
        enum ItemState : uint8_t
        {
            free = 0,       // 空闲，可写入
            reserved,       // 已写入，等待消费者读取
            readyToPop      // 已读取，等待消费者弹出释放
        };

        // 初始化槽位：状态归零，数据清零
        void init()
        {
            enumState = free;
            ::common::utils::zeroInitialize(item);
        }

        // 原子推进槽位状态（fetch_add(1)），并在溢出时 CAS 归零
        // 状态循环：free(0) → reserved(1) → readyToPop(2) → free(0)
        void nextState(l2lo::common::Atomic<uint8_t>& state) const
        {
            const auto prevState = state.fetch_add(1);
            // 若之前是 readyToPop(2)，则 fetch_add 后变为 3，需要 CAS 归零回 free(0)
            if (prevState == ItemState::readyToPop)
            {
                uint8_t tempState = 0;
                uint8_t expectedState = ItemState::readyToPop + 1; // 期望值为 3
                const bool succeeded = state.compare_exchange_weak(expectedState, tempState);
                if (not succeeded)
                {
                    // CAS 失败说明有并发竞争，可能存在数据同步问题
                    printf("Possible data synchronization issue with L2-LO ringbuffer - state changing");
                }
            }
        }

        l2lo::common::Atomic<uint8_t> enumState{free}; // 槽位状态（原子）
        ValueType item;                                  // 存储的数据
    };

    // 受保护构造函数：由子类调用，注入外部存储数组
    // array: 底层存储数组（由子类持有）
    // bufferSize: 实际使用的容量，不得超过 arraySize
    FixedSizeSimpleRingBuffer(std::array<ArrayItem, arraySize>& array, const uint32_t bufferSize)
        : maxSize{bufferSize > arraySize ? arraySize : bufferSize}, ringBuffer{array}
    {
        if (bufferSize > arraySize)
        {
            printf("bufferSize must be <= than arraySize");
        }

        initializeBuffer();
    }

private:
    // 将所有槽位重置为 free，并将三个索引归零
    void initializeBuffer()
    {
        for (uint32_t i = 0; i < maxSize; i++)
        {
            ringBuffer.at(i).init();
        }
        nextWriteIndex = 0;
        nextReadIndex = 0;
        nextPopIndex = 0;
    }

    // 缓冲区已满：写索引与弹出索引重合，且该槽位状态不为 free（说明已被占用）
    [[nodiscard]] bool isFull() const
    {
        return (nextWriteIndex.load() == nextPopIndex.load()) and
            (ringBuffer.at(nextWriteIndex).enumState.load() != ArrayItem::ItemState::free);
    }

    // 所有已写入的元素都未被读取：写索引与读索引重合，且该槽位为 reserved
    [[nodiscard]] bool areAllElementsToRead() const
    {
        return (nextWriteIndex.load() == nextReadIndex) and
            (ringBuffer.at(nextReadIndex).enumState.load() == ArrayItem::ItemState::reserved);
    }

    // 无元素可读：写索引与读索引重合，且该槽位为 free（表示没有写入数据）
    [[nodiscard]] bool isNothingToRead() const
    {
        return (nextWriteIndex.load() == nextReadIndex) and
            (ringBuffer.at(nextReadIndex).enumState.load() == ArrayItem::ItemState::free);
    }

    // 推进读索引（非原子，单读者），返回推进前的索引值
    [[nodiscard]] uint32_t incReadIndex()
    {
        const uint32_t currentIndex = nextReadIndex;
        nextReadIndex++;
        // 环形回绕
        if (nextReadIndex >= maxSize)
        {
            nextReadIndex = 0;
        }
        return currentIndex;
    }

    // 原子推进写索引，并在到达末尾时 CAS 归零（环形回绕）
    // 返回推进前的索引值，供调用方写入数据
    [[nodiscard]] uint32_t incWriteIndex()
    {
        const uint32_t currentIndex = nextWriteIndex.fetch_add(1);
        if (currentIndex >= (maxSize - 1))
        {
            // fetch_add 后索引变为 maxSize，用 CAS 将其归零
            auto expectedIndex = maxSize;
            const bool succeeded = nextWriteIndex.compare_exchange_weak(expectedIndex, 0);
            if (not succeeded)
            {
                printf("Possible data synchronization issue with L2-LO ringbuffer - write index");
            }
        }
        return currentIndex;
    }

    // 原子推进弹出索引，并在到达末尾时 CAS 归零（环形回绕）
    [[nodiscard]] uint32_t incPopIndex()
    {
        const uint32_t currentIndex = nextPopIndex.fetch_add(1);
        if (currentIndex >= (maxSize - 1))
        {
            auto expectedIndex = maxSize;
            const bool succeeded = nextPopIndex.compare_exchange_weak(expectedIndex, 0);
            if (not succeeded)
            {
                printf("Possible data synchronization issue with L2-LO ringbuffer - pop index");
            }
        }
        return currentIndex;
    }

    // 当前读索引处的槽位是否处于 reserved 状态（有数据可读）
    [[nodiscard]] bool itemsToRead() const
    {
        return (ringBuffer.at(nextReadIndex).enumState.load() == ArrayItem::ItemState::reserved);
    }

    uint32_t nextReadIndex{0};                    // 读索引（非原子，单读者）
    l2lo::common::Atomic<uint32_t> nextWriteIndex{0u};  // 写索引（原子，支持并发写入）
    l2lo::common::Atomic<uint32_t> nextPopIndex{0u};    // 弹出索引（原子，支持并发弹出）

    const uint32_t maxSize;                              // 运行时生效的缓冲区容量（≤ arraySize）
    std::array<ArrayItem, arraySize>& ringBuffer;        // 底层存储数组（外部持有，本类仅引用）
};
```

---

## 关键设计要点

### 1. 为何不用默认构造函数？

底层数组 `std::array<ArrayItem, arraySize>` 由子类持有，本类通过受保护构造函数注入引用。这样避免了本类直接持有大型数组，便于子类通过栈内存或共享内存提供存储。

### 2. 写索引环形回绕的 CAS 细节

`incWriteIndex()` 使用 `fetch_add(1)` 原子递增，当结果达到 `maxSize` 时，用 `compare_exchange_weak` 将其 CAS 回 0。这种方式比先检查再赋值的方式更安全，但要求同时只有一个写者发起回绕（若多写者并发，仍有竞争风险）。

### 3. `nextReadIndex` 非原子

读操作为单读者设计，`nextReadIndex` 是普通 `uint32_t`，没有原子开销。若需要多读者，需将其改为原子变量并适配 `incReadIndex()` 的回绕逻辑。

### 4. `itemsToRead` 的判断方式

不使用索引比较，而是直接检查槽位状态是否为 `reserved`，这更精确——避免了索引相等时满/空的歧义判断。

---

## Ping-Pong Buffer 外层封装

### 设计动机

`FixedSizeSimpleRingBuffer` 的生产者和消费者共享同一块内存（同一个数组），依靠槽位状态机和原子索引来协调访问。这在**极高并发或实时性要求**的场景下可能仍存在隐患：

- 生产者写入某槽位时，消费者可能正在读取相邻槽位，存在 **false sharing（伪共享）** 风险
- 消费者读取期间，生产者可能已在同一缓冲区写入新数据，消费者看到的视图不一致

**Ping-Pong Buffer（乒乓缓冲）** 通过维护两个独立的缓冲区来解决这个问题：

- **活跃缓冲区（active）**：生产者当前的写入目标
- **非活跃缓冲区（inactive）**：消费者当前的读取来源
- `swap()` 原子地切换活跃/非活跃角色，实现**完全的空间隔离**

### 架构图

```
  生产者                                          消费者
    │                                               │
    │  push()                                       │  pop() / readCurrentElement()
    ▼                                               ▼
┌─────────────────────┐    swap()    ┌─────────────────────┐
│  buffers[active]    │ ──────────→  │  buffers[inactive]  │
│  (写入侧 RingBuffer) │ ←──────────  │  (读取侧 RingBuffer) │
└─────────────────────┘              └─────────────────────┘
         ▲                                         │
         └─── swap() 后：inactive.clear()，        │
              activeIndex 原子翻转                 │
                                            消费者继续消费
                                            上一批写入的数据
```

### swap() 时序

```
时刻 T0：active=0，生产者写 buffers[0]，消费者读 buffers[1]
时刻 T1：生产者调用 swap()
           → buffers[1].clear()（清空旧消费侧，准备接收新数据）
           → activeIndex.fetch_xor(1)  →  active=1
时刻 T2：生产者写 buffers[1]，
         消费者读 buffers[0]（读取 T0 期间写入的那批数据）
```

### 代码实现

```cpp
// ── 第一步：具体化 RingBuffer，使其自持存储数组，可直接实例化 ──────────────
// FixedSizeSimpleRingBuffer 的构造函数是 protected 且禁止默认构造，
// 通过继承并在子类中持有数组来突破这一限制。
template <typename ValueType, uint32_t bufferSize>
class ConcreteRingBuffer : public FixedSizeSimpleRingBuffer<ValueType, bufferSize>
{
    using Base      = FixedSizeSimpleRingBuffer<ValueType, bufferSize>;
    using ArrayItem = typename Base::ArrayItem; // protected 成员，子类可访问

public:
    // 将自持的 storage 数组注入父类，父类只持有引用
    ConcreteRingBuffer() : Base(storage, bufferSize) {}

private:
    std::array<ArrayItem, bufferSize> storage; // 实际存储，生命周期由本类管理
};


// ── 第二步：PingPongRingBuffer — 双缓冲外层封装 ───────────────────────────
// 生产者写活跃缓冲区，消费者读非活跃缓冲区，swap() 切换角色。
template <typename ValueType, uint32_t bufferSize>
class PingPongRingBuffer
{
    using Buffer = ConcreteRingBuffer<ValueType, bufferSize>;

public:
    // 生产者接口：写入当前活跃缓冲区
    void push(const ValueType& value)
    {
        active().push(value);
    }

    // 消费者接口：从非活跃缓冲区一步读取并释放槽位
    [[nodiscard]] bool pop(ValueType& value)
    {
        return inactive().pop(value);
    }

    // 消费者接口：只读取，不推进索引（用于"窥视"）
    [[nodiscard]] bool readCurrentElement(ValueType& value)
    {
        return inactive().readCurrentElement(value);
    }

    // 消费者接口：读取完毕后推进索引，将槽位标记为 readyToPop
    [[nodiscard]] bool advanceReadIndex()
    {
        return inactive().advanceReadIndex();
    }

    // 切换双缓冲：
    //   1. 清空即将变为"写入侧"的缓冲区（当前消费侧），避免旧数据污染
    //   2. acq_rel 语义确保清空操作对切换后的生产者可见，
    //      同时切换前的写入对切换后的消费者可见
    void swap()
    {
        inactive().clear();
        activeIndex.fetch_xor(1u, std::memory_order_acq_rel);
    }

    // 重置两个缓冲区
    void clearAll()
    {
        buffers[0].clear();
        buffers[1].clear();
        activeIndex.store(0u, std::memory_order_release);
    }

private:
    // acquire 确保后续对 buffer 的访问不会被重排到 load 之前
    Buffer& active()
    {
        return buffers[activeIndex.load(std::memory_order_acquire)];
    }
    Buffer& inactive()
    {
        return buffers[1u - activeIndex.load(std::memory_order_acquire)];
    }

    std::array<Buffer, 2>          buffers;           // [0]=ping, [1]=pong
    std::atomic<uint32_t>          activeIndex{0u};   // 0 或 1，指示当前写入侧
};
```

### 使用示例

```cpp
// 容量为 64 的 double-buffered 消息队列
PingPongRingBuffer<MyMessage, 64> ppBuf;

// 生产者线程
void producer()
{
    for (auto& msg : batch)
    {
        ppBuf.push(msg);        // 写入 active 缓冲区
    }
    ppBuf.swap();               // 批次结束，切换缓冲区
}

// 消费者线程（在 swap() 之后运行，或持续轮询）
void consumer()
{
    MyMessage msg;
    while (ppBuf.pop(msg))      // 从 inactive 缓冲区消费
    {
        process(msg);
    }
}
```

### 与单缓冲 RingBuffer 的对比

| 特性 | `FixedSizeSimpleRingBuffer` | `PingPongRingBuffer` |
|---|---|---|
| 缓冲区数量 | 1 | 2（ping + pong） |
| 空间隔离 | 无（共享同一数组） | 完全隔离（读写各用一块） |
| 内存占用 | N × sizeof(item) | 2N × sizeof(item) |
| 适合场景 | 细粒度流式传输 | 按批次生产、消费者需要稳定视图 |
| 切换开销 | 无 | 每批一次 `swap()` + `clear()` |
| false sharing 风险 | 存在 | 消除 |
