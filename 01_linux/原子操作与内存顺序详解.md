# 1. memory_order_relaxed
最宽松的内存顺序，只保证操作本身是原子的，不提供任何同步或顺序保证。
```cpp
// 线程1                          // 线程2
x.store(1, relaxed);              y.store(1, relaxed);
r1 = y.load(relaxed);             r2 = x.load(relaxed);
// 可能结果：r1=0, r2=0  （违反直觉但合法！）
```
适用场景：

* 只需要原子性，不关心顺序（如计数器）
* cumulativeBlockCount.fetch_add(1, relaxed) — 只需保证计数不丢失

# 2. memory_order_release
释放语义：当前线程中，此操作之前的所有读写，对获取（acquire）此原子变量的线程可见。
```cpp
// 线程1 (生产者)
data = 42;                                    // 普通写
ready.store(true, memory_order_release);      // release屏障 ▼

// 线程2 (消费者)
while (!ready.load(memory_order_acquire));    // acquire屏障 ▲
assert(data == 42);                           // 保证看到42
```
图示
```cpp
线程1:  [data=42] [其他写入] ──release──▶ store(ready)
                                              │
                                              ▼ 同步点
线程2:                          load(ready) ──acquire──▶ [读data] 保证看到之前的写入
```

# 3. compare_exchange_weak
CAS（Compare-And-Swap）操作：原子地比较并交换。
```cpp
bool compare_exchange_weak(T& expected, T desired,
                           memory_order success,
                           memory_order failure)
```
工作流程：
```cpp
if (原子变量 == expected) {
    原子变量 = desired;  // 成功，使用success内存顺序
    return true;
} else {
    expected = 原子变量;  // 失败，更新expected为当前值
    return false;         // 使用failure内存顺序
}
```

weak vs strong：
|版本	|  特点 |
|----   | ----- |
|weak	| 可能伪失败（即使相等也可能返回false），但更快 |
|strong	 | 不会伪失败，但可能略慢 |

为什么用weak：在循环中使用时，伪失败无所谓，下次循环会重试。

# 4. 代码中的内存顺序分析
```cpp
// ① relaxed加载：只需获取当前值，无需同步
uint32_t writeIndex = headerAddress->writeIndex.load(std::memory_order_relaxed);

do {
    ptr = &(blocks[writeIndex]);
    nextIndex = ...;
    
// ② CAS操作
} while (!headerAddress->writeIndex.compare_exchange_weak(
    writeIndex,           // expected（失败时被更新）
    nextIndex,            // desired
    memory_order_release, // 成功时：确保ptr赋值对其他线程可见
    memory_order_relaxed  // 失败时：只需重新读取，无需同步
));
```

为什么成功用release：

* 成功分配后，调用者会写入blocks[writeIndex]
* 其他线程（如读取者）通过acquire读取writeIndex后，能看到完整的块内容

# 5. 可视化总结
```cpp
┌─────────────────────────────────────────────────────────────┐
│                    内存顺序强度                              │
├─────────────────────────────────────────────────────────────┤
│  relaxed ◀──────────────────────────────────────▶ seq_cst   │
│  (最弱)     acquire/release     acq_rel          (最强)     │
│                                                             │
│  性能:  最好 ◀────────────────────────────────▶ 最差        │
│  保证:  最少 ◀────────────────────────────────▶ 最多        │
└─────────────────────────────────────────────────────────────┘
```

|顺序	|用途|
|---    |----|
|relaxed	|计数器、标志位（不关心顺序）|
|acquire	|读取共享数据前，确保看到release之前的写入|
|release	|写入共享数据后，确保之前的写入对acquire可见|
|seq_cst	|全局顺序一致（默认，最安全但最慢）|
