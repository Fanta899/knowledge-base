# C++ 后端服务器面试 —— B 类：并发与线程模型 标准答案

> 来源：`cpp-backend-server-interviewer.prompt.md`  
> 本文件按面试官视角给出**核心要点 / 常见误区 / 加分点 / 代码示例**四个维度的完整参考答案。

---

## B1. 后端服务线程池核心数如何确定？CPU 密集型 vs IO 密集型任务的公式与实践

### 核心要点

1. **CPU 密集型任务**：线程数 ≈ CPU 核心数（逻辑核）+ 1。多出的 1 条线程用于在某个线程因缺页中断等短暂暂停时保持 CPU 满载。
2. **IO 密集型任务**：线程数 = CPU 核心数 × (1 + 等待时间 / 计算时间)。等待比越高，线程数越多，经验值通常是核数的 2~4 倍。
3. **混合型任务**：可拆分为两个独立的线程池，CPU 密集线程池 + IO 密集线程池，避免 IO 等待线程占据 CPU 核心。
4. **实测优于公式**：公式给出初始值，最终需通过压测（如 wrk / ab）配合 `htop` / `perf` 观察 CPU 利用率、上下文切换次数（`vmstat 1`）来调整。
5. **上下文切换开销**：线程数过多时，`cs`（context switch）飙升，`sys` 占比增加，真实吞吐量反而下降。可用 `perf stat -e context-switches` 量化。

### 常见误区

- 认为线程数越多越好，忽视上下文切换开销和 cache 抖动（false sharing、TLB miss）。
- 对所有业务用同一个线程池，导致慢 IO 任务拖死 CPU 计算任务。
- 忽略 NUMA 架构：跨 NUMA 节点的内存访问延迟是本地访问的 2~3 倍，线程池应尽量绑核到同一 NUMA 节点（`numactl --cpunodebind`）。

### 加分点

- 提到 **Amdahl 定律**：并行加速比存在上限，序列化瓶颈（如锁）会限制有效线程数。
- 提到 **动态线程池**（如 Java `ThreadPoolExecutor` 的 `corePoolSize` / `maximumPoolSize`，C++ 端可用 `std::atomic` 计数动态扩缩）。
- 提到 **brpc 的 bthread 模型**：通过 M:N 协程将 IO 等待折叠在用户态，可以用少量 pthread 撑起大量并发，彻底绕开线程数调优问题。

### 代码示例

```cpp
// 获取 CPU 逻辑核数
unsigned int n_cores = std::thread::hardware_concurrency();

// CPU 密集型线程池
size_t cpu_pool_size = n_cores + 1;

// IO 密集型（假设等待/计算比 = 3）
size_t io_pool_size = n_cores * (1 + 3);  // = 4 * n_cores
```

---

## B2. 工作窃取（work-stealing）线程池与单全局队列线程池的适用场景；`std::deque` 局部性问题

### 核心要点

1. **单全局队列线程池**：所有工作线程共享一把锁 + 一个任务队列。实现简单，但在高并发时锁竞争严重，CPU 缓存行频繁失效（false sharing），适合低并发或任务粒度大的场景。
2. **Work-stealing 线程池**：每个线程持有独立的本地双端队列（deque），自己从队头取任务（LIFO，利用 cache 热度），空闲时从其他线程队尾窃取任务（FIFO，减少对目标线程的影响）。
3. **Work-stealing 优势**：减少全局锁竞争；LIFO 取本地任务利用 CPU L1/L2 cache 热度；任务产生子任务（分治/递归）时，子任务留在本地队列，时间局部性好（如 Intel TBB、Go runtime 的 goroutine 调度器）。
4. **适用场景对比**：
   - 单全局队列：任务无依赖、粒度均匀、并发量低（< 核数的 2 倍）。
   - Work-stealing：分治任务（如并行 sort、树遍历）、任务粒度差异大、核数多（8 核以上）。
5. **`std::deque` 的局部性问题**：`std::deque` 的实现是分段数组（chunk buffer），元素不连续，遍历时 cache miss 率高。Work-stealing 场景更倾向用循环数组（ring buffer）+ CAS 实现的 lock-free deque（如 Chase-Lev 算法）。

### 常见误区

- 认为 work-stealing 一定优于全局队列——当任务粒度极小（纳秒级）时，窃取操作的 CAS 开销反而成为瓶颈。
- 忽略 work-stealing 的实现复杂度：需要 lock-free deque，正确处理内存顺序（ABA 问题）。

### 加分点

- 提到 **Chase-Lev lock-free deque**（2005 年论文），owner 用 relaxed CAS，stealer 用 strong CAS。
- 提到 **Intel TBB**（`tbb::task_group`）和 **Rust rayon** 都基于 work-stealing。
- 提到 **亲和性调度**：窃取时优先从"最近邻"线程（同 NUMA 节点、同 L3 cache 域）窃取，减少跨 NUMA 内存访问。

### 代码示例（Chase-Lev deque 概念性伪码）

```cpp
// 本地 LIFO push/pop（owner 线程操作 bottom）
void push(Task t) {
    bottom_.store(b + 1, relaxed);
    buf_[b] = t;
    std::atomic_thread_fence(release);
}

// 其他线程 FIFO steal（操作 top）
std::optional<Task> steal() {
    size_t t = top_.load(acquire);
    size_t b = bottom_.load(acquire);
    if (t >= b) return std::nullopt;
    Task task = buf_[t % cap_];
    if (!top_.compare_exchange_strong(t, t + 1, seq_cst))
        return std::nullopt;  // 被抢占
    return task;
}
```

---

## B3. 异步任务链（task chaining）的实现：`std::future` / `std::promise` vs 协程（C++20 coroutine）的对比

### 核心要点

1. **`std::future` / `std::promise`**：
   - `promise` 是生产者端，`future` 是消费者端，通过共享状态（shared state）传递值或异常。
   - `std::async` 返回 `future`，可用 `future::then`（实验性）或手动 `.get()` 构建链式调用，但 `.get()` 会阻塞线程。
   - 任务链必须手动管理回调嵌套（Callback Hell），可读性差，错误传播复杂。

2. **`std::future` 的缺陷**：
   - 不可组合（没有标准 `then`，C++23 `std::execution` P2300 才引入 sender/receiver）。
   - `future::get()` 调用会阻塞当前线程，无法在单线程事件循环（如 Reactor）中使用。
   - 不支持协作式取消（C++20 `std::stop_token` 可弥补，但 `future` 本身不集成）。

3. **C++20 协程（coroutine）**：
   - 基于 `co_await` / `co_return` / `co_yield`，编译器生成状态机，挂起时不阻塞线程，恢复时继续执行。
   - 任务链天然是顺序代码，可读性与同步代码相同（"写同步、跑异步"）。
   - 错误传播通过 `co_await` 的 `promise_type::unhandled_exception()` 自动传递。
   - 代表库：`cppcoro`、`libunifex`、`asio::awaitable`（Asio + C++20）。

4. **性能对比**：
   - 协程帧通常在堆上分配（可由编译器优化到栈，Heap Elision Optimization）。
   - 协程切换开销 < 线程上下文切换（纳秒级 vs 微秒级），适合 IO 密集型服务。

5. **适用场景**：
   - `future/promise`：少量异步操作、跨线程一次性同步（如主线程等待子任务完成）。
   - 协程：高并发 IO 密集型服务，任务链长，需要低延迟和高吞吐。

### 常见误区

- 认为 `std::async` 就是协程——`async` 仍是基于线程的，`get()` 会阻塞。
- 在事件循环线程中调用 `future.get()` 导致死锁。
- 协程调度器设计不当，导致协程在不同线程间迁移时数据竞争。

### 加分点

- 提到 **C++23 `std::execution`（P2300）**：引入 `sender` / `receiver` / `scheduler` 三元组，将异步组合提升为标准语义。
- 提到 **Asio 的 `co_spawn` + `awaitable`** 是目前 C++ 工程中最成熟的协程异步方案。
- 提到协程帧的 **Heap Elision**：若编译器能证明协程生命周期不超出调用者，可将协程帧分配到栈上（GCC/Clang 均已支持）。

### 代码示例

```cpp
// C++20 协程任务链（Asio awaitable 风格）
asio::awaitable<std::string> fetch_and_process(asio::io_context& ioc) {
    // co_await 挂起当前协程，不阻塞线程
    auto raw = co_await async_http_get("http://example.com/api", asio::use_awaitable);
    auto parsed = co_await async_parse_json(raw, asio::use_awaitable);
    co_return parsed["result"].get<std::string>();
}

// future 方式（会阻塞线程）
std::future<std::string> fut = std::async(std::launch::async, [](){
    auto raw  = sync_http_get("http://example.com/api");  // 阻塞
    return parse_json(raw)["result"].get<std::string>();
});
std::string result = fut.get();  // 再次阻塞
```

---

## B4. 后端服务中的定时任务调度：时间轮 vs `std::priority_queue`；毫秒级精度的实现要点

### 核心要点

1. **`std::priority_queue`（最小堆）方案**：
   - 每个定时器是一个 `(到期时间, 回调)` 对，按到期时间排序。
   - `push` O(log n)，`pop` O(log n)，`top` O(1)。
   - 适合定时器数量少（< 万级）、精度要求不高的场景。
   - 线程唤醒：用 `std::condition_variable::wait_until` 精确等待到堆顶到期时间。

2. **时间轮（Timing Wheel）方案**：
   - 将时间轴分成固定槽（slot），每个槽对应一个时间精度（如 1ms）。
   - 简单时间轮：环形数组，大小 N（如 1000），当前指针每 tick 前进一格，触发该槽中的所有定时器。插入 O(1)，删除 O(1)（用链表），触发 O(到期回调数)。
   - 层级时间轮（Hierarchical Timing Wheel，如 Linux 内核 `timer_list`）：多层（ms/s/min/hr），低层溢出时级联到上层，处理超长超时而不需要巨大数组。
   - 适合定时器数量极多（百万级，如 TCP 超时管理）、精度固定的场景。

3. **毫秒级精度要点**：
   - 避免使用 `sleep` / `usleep`（精度差，受系统调度影响）。
   - 用 `clock_gettime(CLOCK_MONOTONIC, ...)` 获取高精度单调时钟（不受 NTP 跳变影响）。
   - `timerfd_create` + epoll：内核定时器通过文件描述符触发，精度可达微秒级，与事件循环无缝集成。
   - `CLOCK_MONOTONIC` vs `CLOCK_REALTIME`：后者会因 NTP 调整发生跳变，定时器必须用前者。

4. **对比总结**：

| | 优先队列 | 时间轮 |
|---|---|---|
| 插入 | O(log n) | O(1) |
| 删除 | O(log n) | O(1) |
| 适合规模 | < 万级 | 百万级 |
| 实现复杂度 | 低 | 中（层级时间轮高） |
| 代表使用 | 业务定时任务 | Nginx/内核 TCP 超时 |

### 常见误区

- 使用 `std::this_thread::sleep_for` 实现定时器，不考虑处理时间漂移累积误差。
- 时间轮槽数设置过小，导致长超时任务需要记录"剩余圈数"但未正确实现。
- 在事件驱动框架中用单独线程轮询定时器，而非用 `timerfd` 与 epoll 集成。

### 加分点

- 提到 **`timerfd_create(CLOCK_MONOTONIC) + timerfd_settime + epoll`** 是事件驱动框架中最优雅的定时器实现。
- 提到 **Linux 内核的层级时间轮**演进（从 5-level 到 dyntick 模式）。
- 提到 **Kafka 的 `TimingWheel`** 实现（Scala，层级 + 延迟队列）。

### 代码示例

```cpp
// timerfd + epoll 集成定时器（精度微秒级）
int tfd = timerfd_create(CLOCK_MONOTONIC, TFD_NONBLOCK | TFD_CLOEXEC);

struct itimerspec its{};
its.it_value.tv_sec  = 0;
its.it_value.tv_nsec = 10'000'000;  // 首次 10ms 后触发
its.it_interval.tv_nsec = 10'000'000; // 之后每 10ms 触发一次
timerfd_settime(tfd, 0, &its, nullptr);

// 将 tfd 加入 epoll 监听，超时时 read(tfd, &expirations, 8) 获取过期次数
```

---

## B5. 读写锁（`shared_mutex`）在缓存更新场景中的使用；写者饥饿问题与解决方案

### 核心要点

1. **`std::shared_mutex`（C++17）**：
   - 读者用 `shared_lock<shared_mutex>`，多个读者可并发持有。
   - 写者用 `unique_lock<shared_mutex>`，独占访问，阻塞所有读者和其他写者。
   - 适合读多写少（读/写比 > 10:1）的缓存场景。

2. **缓存更新典型模式**：
   ```cpp
   std::shared_mutex rw_mutex;
   std::unordered_map<std::string, Value> cache;

   Value get(const std::string& key) {
       std::shared_lock lock(rw_mutex);  // 并发读
       return cache.at(key);
   }
   void update(const std::string& key, Value val) {
       std::unique_lock lock(rw_mutex);  // 独占写
       cache[key] = std::move(val);
   }
   ```

3. **写者饥饿（Writer Starvation）**：
   - 若读者持续涌入，写者可能长期无法获取锁。
   - `shared_mutex` 的实现策略影响公平性：部分实现（如 glibc）写者到来后阻止新读者进入，保证写者优先；另一些实现不保证，可能出现写者饥饿。

4. **解决写者饥饿的方案**：
   - **写者优先策略**：维护等待写者计数，新读者申请时若有等待写者则排队等待。
   - **读写比限制**：写者等待超过阈值后强制排他（降级为互斥锁）。
   - **Phase Fair Lock**：按读/写阶段交替公平调度，确保写者不饥饿。
   - **分片锁（Sharded Lock）**：将缓存分成 N 个分片，每个分片独立读写锁，写者只锁定目标分片，大幅减少竞争。

5. **性能注意事项**：
   - `shared_mutex` 比 `mutex` 重（内部含计数器），低竞争时可能比 `mutex` 慢。
   - 读者临界区尽量短（只取值，计算放在锁外）。
   - 读/写比 < 5:1 时，直接用 `mutex` 可能更快（避免 shared_mutex 的原子计数开销）。

### 常见误区

- 对只读操作也用 `unique_lock`，丧失并发优势。
- 在锁内执行耗时操作（如序列化、IO），导致写者长时间持锁。
- 误以为 `std::shared_mutex` 一定写者优先，未实测具体平台行为。

### 加分点

- 提到 **RCU（Read-Copy-Update）**：Linux 内核经典技术，读者零开销（无锁），写者复制后原子替换指针，适合极端读多写少场景（读/写比 > 1000:1），C++ 可用 `std::shared_ptr` 模拟。
- 提到 **`folly::RWSpinLock`** 或 **`absl::Mutex`** 提供的更优写者优先实现。
- 提到分片锁（striped lock）：`std::array<std::shared_mutex, N> shards`，用 `hash(key) % N` 分发，N 取 CPU 核数的 2 倍效果最佳。

### 代码示例（RCU 风格用 shared_ptr 模拟）

```cpp
// 读多写少，RCU 风格：读者无锁，写者原子替换
std::shared_ptr<const std::unordered_map<std::string, Value>> g_cache
    = std::make_shared<std::unordered_map<std::string, Value>>();

// 读：无锁 load，引用计数保护
Value get(const std::string& key) {
    auto snapshot = std::atomic_load(&g_cache);  // 原子 load
    return snapshot->at(key);
}

// 写：copy-on-write，原子替换
void update(const std::string& key, Value val) {
    std::unique_lock lock(write_mutex_);  // 写者间互斥
    auto new_cache = std::make_shared<std::unordered_map<std::string, Value>>(*std::atomic_load(&g_cache));
    (*new_cache)[key] = std::move(val);
    std::atomic_store(&g_cache, new_cache);  // 原子替换
}
```

---

## B6. `std::condition_variable` 的虚假唤醒（spurious wakeup）；后端服务中正确的等待模式

### 核心要点

1. **虚假唤醒（Spurious Wakeup）定义**：线程在没有任何 `notify_one` / `notify_all` 调用的情况下，从 `wait` 中返回。这是 POSIX `pthread_cond_wait` 规范允许的行为，底层原因是某些硬件架构和内核实现的约束。

2. **正确的等待模式**：必须用 `while` 循环（或 `wait` 的谓词重载）检查条件：

   ```cpp
   // 错误：if + wait，虚假唤醒后条件可能未满足
   std::unique_lock<std::mutex> lock(mtx);
   if (!ready) cv.wait(lock);   // ❌ 危险

   // 正确：while 循环
   while (!ready) cv.wait(lock);  // ✅ 或

   // 更优：谓词重载（等价于 while 循环）
   cv.wait(lock, [&]{ return ready; });  // ✅ 推荐
   ```

3. **后端服务的正确等待模式**：任务队列生产者-消费者场景：

   ```cpp
   // 消费者
   std::unique_lock lock(mtx);
   cv_not_empty.wait(lock, [&]{ return !queue.empty() || stopped; });
   if (stopped && queue.empty()) return;  // 优雅关闭
   auto task = queue.front(); queue.pop();
   ```

4. **`wait_for` / `wait_until` 的超时返回值**：返回 `std::cv_status::timeout` 或 `std::cv_status::no_timeout`，超时后必须重新检查条件（同样可能虚假唤醒）。

5. **`notify_one` vs `notify_all`**：
   - `notify_one`：唤醒一个等待者，适合生产者-消费者（每次只有一个消费者需要被唤醒）。
   - `notify_all`：唤醒所有等待者，适合"广播"（如配置变更通知所有工作线程），但可能造成"惊群"效应，需配合 `while` 重新竞争。

### 常见误区

- 用 `if` 代替 `while`/谓词，虚假唤醒后直接操作共享数据导致 UB。
- 在持有锁时调用 `notify_*`（不是错误，但持锁通知可能导致被唤醒线程立即再次阻塞等待锁，推荐先解锁再通知）。
- `wait` 返回后忘记检查 `stopped` 标志，导致线程池关闭后仍有线程持续运行。

### 加分点

- 提到 **`std::atomic` + `wait`（C++20）**：`std::atomic<T>::wait` / `notify_one` / `notify_all` 提供无锁等待通知，底层基于 futex，比 `condition_variable` 轻量。
- 提到 Linux 上 `pthread_cond_wait` 的虚假唤醒来源：`EINTR` 信号中断后内核恢复时会不加区分地返回。
- 提到 **`std::semaphore`（C++20）** 作为特殊情况的替代：固定计数场景比 condition_variable 更直观。

### 代码示例（线程安全任务队列）

```cpp
template<typename T>
class BlockingQueue {
    std::queue<T> q_;
    std::mutex mtx_;
    std::condition_variable cv_not_empty_, cv_not_full_;
    size_t max_size_;
    bool stopped_ = false;
public:
    void push(T item) {
        std::unique_lock lock(mtx_);
        cv_not_full_.wait(lock, [&]{ return q_.size() < max_size_ || stopped_; });
        if (stopped_) throw std::runtime_error("queue stopped");
        q_.push(std::move(item));
        cv_not_empty_.notify_one();
    }

    std::optional<T> pop() {
        std::unique_lock lock(mtx_);
        cv_not_empty_.wait(lock, [&]{ return !q_.empty() || stopped_; });
        if (q_.empty()) return std::nullopt;  // stopped
        T item = std::move(q_.front()); q_.pop();
        cv_not_full_.notify_one();
        return item;
    }

    void stop() {
        { std::lock_guard lock(mtx_); stopped_ = true; }
        cv_not_empty_.notify_all();
        cv_not_full_.notify_all();
    }
};
```

---

## B7. 无锁环形缓冲区（SPSC ring buffer）在日志异步写入中的应用；内存屏障的必要性

### 核心要点

1. **SPSC（Single Producer Single Consumer）环形缓冲区**：仅有一个生产者和一个消费者时，只需 `head`（消费者读取位置）和 `tail`（生产者写入位置）两个原子变量，无需锁。

2. **核心不变量**：
   - 生产者：`tail` 仅由生产者写，消费者只读。
   - 消费者：`head` 仅由消费者写，生产者只读。
   - 满：`(tail + 1) % N == head`；空：`head == tail`。

3. **内存屏障的必要性**：
   - 编译器和 CPU 可能对内存访问重排序。
   - 生产者写入数据后，必须有 **release** 屏障，确保数据写入对消费者可见后，`tail` 的更新才对消费者可见。
   - 消费者读取 `tail` 时必须有 **acquire** 屏障，确保在读到 `tail` 的新值后，才能读取对应的数据（happen-before 关系）。
   - 不加内存屏障，消费者可能看到更新后的 `tail` 但读到旧数据（reorder 导致）。

4. **`alignas(64)` 的必要性**：`head` 和 `tail` 分别放在不同的 64 字节 cache line，避免 false sharing（生产者更新 `tail` 会使消费者的 `head` cache line 失效，反之亦然）。

5. **日志异步写入应用**：
   - 前台业务线程（生产者）写日志到 ring buffer，几乎无阻塞。
   - 后台 IO 线程（消费者）批量从 ring buffer 读取，合并写入磁盘（减少 `write` 系统调用次数，利用 page cache）。
   - 典型实现：`spdlog` 的异步模式、`NanoLog`。

### 常见误区

- 用 `std::memory_order_relaxed` 更新 `tail` 后直接让消费者读取数据，缺少 release/acquire 配对，导致数据竞争（UB）。
- `head` 和 `tail` 放在同一 cache line，producer/consumer 频繁互相使 cache 失效，吞吐量下降 3~10 倍。
- 缓冲区满时生产者忙等（spin），应有退化策略（丢弃低优先级日志 / 降级同步写入）。

### 加分点

- 提到 **`std::atomic<size_t>` with `memory_order_acquire/release`** 是 C++ 标准的正确做法，比 `volatile` + 手工 `__sync_synchronize()` 更可移植。
- 提到 **x86 的 TSO（Total Store Order）内存模型**：x86 上 store-load 是唯一需要 mfence 的屏障，store-store/load-load 已经有序，因此 x86 上 release/acquire 有时可编译为 `MOV`（无额外指令），但 ARM 需要 `stlr`/`ldar` 指令。
- 提到 **power-of-2 大小的 ring buffer** 可以用 `& (N-1)` 代替取模，消除除法指令。

### 代码示例

```cpp
template<typename T, size_t N>
class SPSCQueue {
    static_assert((N & (N - 1)) == 0, "N must be power of 2");

    alignas(64) std::atomic<size_t> head_{0};  // 消费者独占 cache line
    alignas(64) std::atomic<size_t> tail_{0};  // 生产者独占 cache line
    T buf_[N];

public:
    // 生产者调用
    bool push(T val) {
        size_t t = tail_.load(std::memory_order_relaxed);
        if ((t - head_.load(std::memory_order_acquire)) == N) return false;  // 满
        buf_[t & (N - 1)] = std::move(val);
        tail_.store(t + 1, std::memory_order_release);  // release：确保数据先于 tail 可见
        return true;
    }

    // 消费者调用
    bool pop(T& val) {
        size_t h = head_.load(std::memory_order_relaxed);
        if (h == tail_.load(std::memory_order_acquire)) return false;  // 空
        val = std::move(buf_[h & (N - 1)]);
        head_.store(h + 1, std::memory_order_release);
        return true;
    }
};
```

---

## B8. ⭐ C++20 协程在后端 IO 密集型服务中的优势；协程调度器（scheduler）的设计要点

### 核心要点

1. **C++20 协程的优势**：
   - **零阻塞线程**：IO 等待时 `co_await` 挂起协程，线程立即去处理其他协程，无上下文切换开销。
   - **低内存占用**：协程帧通常几十到几百字节（仅保存局部变量），远小于线程栈（默认 8MB）。单机可轻松维护百万级并发协程。
   - **代码可读性**：以同步风格编写异步逻辑，消除回调地狱，错误处理用 try/catch 而非层层嵌套。
   - **零拷贝组合**：`co_await` 可组合，子协程 `co_await` 父协程中，自然形成调用链。

2. **C++20 协程的三个关键概念**：
   - `promise_type`：控制协程行为（初始挂起、最终挂起、异常处理、返回值）。
   - `coroutine_handle`：协程句柄，可 `resume()` / `destroy()`。
   - `Awaitable`：实现 `await_ready` / `await_suspend` / `await_resume` 三个接口，定义 `co_await` 的行为。

3. **协程调度器（Scheduler）设计要点**：
   - **任务队列**：调度器维护就绪协程队列（通常是 MPMC 无锁队列或 per-thread run queue）。
   - **IO 集成**：将 epoll/io_uring 的回调与协程恢复（`handle.resume()`）挂钩。IO 完成时，将对应协程的 handle 放入就绪队列，而非直接 resume（避免在 IO 线程中执行业务逻辑）。
   - **线程模型**：
     - 单线程事件循环（Asio `io_context::run()`）：协程串行执行，无锁，适合 CPU 轻量、IO 密集。
     - 线程池 + work-stealing（brpc bthread、Go runtime）：协程可在多核并行执行，适合 CPU 也密集的场景。
   - **协程迁移（Migration）**：协程在 `co_await` 前后可能在不同线程执行，需注意线程局部变量（`thread_local`）和锁的正确性。
   - **优先级调度**：对延迟敏感的协程（如用户请求）分配更高优先级，批量处理协程（如日志刷盘）低优先级。

4. **代表性框架**：
   - **Asio + C++20**：`asio::awaitable<T>` + `co_spawn`，工业级成熟度最高。
   - **cppcoro**：提供 `task<T>`, `generator<T>`, `io_service` 等完整协程基础设施。
   - **brpc bthread**：用户态 M:N 线程（本质是有栈协程），`bthread_start_background` 创建 bthread，同步 API 内部挂起不阻塞 pthread。

### 常见误区

- 误以为 `co_await` 就是 `sleep`，不理解调度器需要主动 `resume` 协程。
- 在协程中使用阻塞 API（如 `std::this_thread::sleep_for`、同步 `read()`），阻塞整个线程，协程优势全失。
- 协程帧中存储大量数据导致堆分配压力（应用 arena allocator 或将大数据移出协程帧）。
- 协程跨线程执行时，误用 `thread_local` 数据。

### 加分点

- 提到 **Heap Elision Optimization（协程帧栈分配优化）**：若编译器能证明协程不逃逸，可在栈上分配帧，无堆分配开销。
- 提到 **`io_uring` + 协程**：`io_uring` 的提交/完成环与协程 `await_suspend` 天然契合——提交 IO 时挂起协程，IO 完成事件触发 resume。
- 提到 **结构化并发（Structured Concurrency）**：`co_spawn` 的父子生命周期绑定，子协程异常自动传播给父协程，避免"火与忘"（fire-and-forget）的生命周期问题。

### 代码示例

```cpp
// 自定义简单 Awaitable（将异步 IO 与协程挂钩）
struct AsyncRead {
    int fd; char* buf; size_t len;
    ssize_t result;

    bool await_ready() const noexcept { return false; }

    void await_suspend(std::coroutine_handle<> h) {
        // 注册到 epoll，IO 就绪时将 h 放入调度器就绪队列
        scheduler.register_read(fd, buf, len, [this, h](ssize_t n) {
            result = n;
            scheduler.enqueue(h);  // 唤醒协程
        });
    }

    ssize_t await_resume() const noexcept { return result; }
};

// 业务协程（看起来像同步代码）
asio::awaitable<void> handle_connection(asio::ip::tcp::socket sock) {
    char buf[4096];
    for (;;) {
        // co_await 挂起协程，线程去处理其他事情
        size_t n = co_await sock.async_read_some(asio::buffer(buf), asio::use_awaitable);
        if (n == 0) break;
        co_await asio::async_write(sock, asio::buffer(buf, n), asio::use_awaitable);
    }
}
```

---

## B9. 线程局部存储（`thread_local`）在连接池、随机数生成器中的应用；生命周期陷阱

### 核心要点

1. **`thread_local` 语义**：每个线程拥有变量的独立副本，生命周期与线程相同。线程启动时初始化，线程退出时析构（按声明的逆序）。

2. **典型应用场景**：
   - **随机数生成器**：`std::mt19937` 线程安全问题——全局单例需要加锁，改用 `thread_local` 每线程一个实例，零竞争。
   - **连接池**：每个线程维护专属的轻量连接缓存（通常 1~2 个连接），避免每次从全局池 check-out/in 加锁。适合线程数固定、连接操作频繁的场景。
   - **日志缓冲区**：每个线程维护独立日志 buffer，定期 flush，避免多线程日志互相竞争。
   - **Arena Allocator**：每请求一个线程本地 arena，请求结束后批量释放，无锁。

3. **生命周期陷阱**：
   - **线程池线程复用问题**：`thread_local` 变量在线程退出前不会重置，线程池中线程处理完任务 A 后处理任务 B，若任务 A 留有脏状态，任务 B 会误读。**必须在每次任务开始时显式重置。**
   - **静态销毁顺序问题**：`thread_local` 变量的析构发生在线程退出时，若其析构依赖其他已销毁的全局/静态变量（如全局日志单例），会产生 UAF（use-after-free）。需确保依赖项生命周期覆盖所有线程。
   - **DLL/动态库加载**：在动态加载的库（`dlopen`）中使用 `thread_local` 可能导致已退出线程的 TLS slot 无法正确析构（平台相关问题）。

4. **实现机制**：编译器通过 `FS` 段寄存器（x86-64）+ Thread Control Block（TCB）实现 TLS，每次访问 `thread_local` 变量需额外一次内存间接寻址（性能影响通常可忽略，但极热路径需测量）。

### 常见误区

- 认为 `thread_local` 的析构时机是"程序退出"，忽视线程退出时的析构，导致析构顺序问题。
- 在线程池复用场景未重置 `thread_local` 状态，产生跨任务数据污染。
- 对 `thread_local` 的指针/引用传递给其他线程使用，该线程退出后指针悬空。

### 加分点

- 提到 **`pthread_key_t` / `pthread_setspecific`**：`thread_local` 的 C API 等价物，可设置线程退出清理函数（`destructor`），比 C++ `thread_local` 析构顺序更可控。
- 提到 **`__thread`（GCC 扩展）vs `thread_local`（C++11 标准）**：`__thread` 不支持非平凡构造/析构类型，`thread_local` 是标准且更强大。
- 提到 **TLS 访问性能**：访问 `thread_local` 在 PIE（Position Independent Executable）可执行文件中有额外 `call __tls_get_addr` 开销，可用 `-ftls-model=initial-exec` 优化（适合非动态加载场景）。

### 代码示例

```cpp
// 线程安全随机数（thread_local 避免加锁）
thread_local std::mt19937 rng(
    std::hash<std::thread::id>{}(std::this_thread::get_id())
);

int random_int(int lo, int hi) {
    return std::uniform_int_distribution<int>{lo, hi}(rng);
}

// 线程池任务复用时的状态重置
thread_local RequestContext tl_ctx;

void process_task(const Request& req) {
    tl_ctx.reset();         // ← 必须显式重置，避免上次任务的脏状态
    tl_ctx.init(req);
    // ... 处理逻辑 ...
}

// 生命周期陷阱示例（危险：依赖全局日志单例）
thread_local struct LogFlusher {
    ~LogFlusher() {
        // 若全局 Logger 已先析构，这里会 UAF
        // GlobalLogger::flush();  // ❌ 危险
    }
} tl_flusher;
```

---

## B10. 后端服务的优雅关闭（graceful shutdown）：如何正确停止线程池、排空任务、关闭连接？

### 核心要点

1. **优雅关闭的目标**：
   - 不丢弃已入队但未处理的任务（保证数据完整性）。
   - 正在处理中的任务允许完成（不强制中断）。
   - 关闭所有对外连接（发送 FIN，让客户端感知服务下线）。
   - 控制关闭超时（超过 deadline 强制退出，防止僵死）。

2. **线程池优雅关闭流程**：
   ```
   1. 设置 stopped = true（原子写）
   2. notify_all 唤醒所有等待任务的线程
   3. 工作线程：发现 stopped 后，继续排空剩余队列再退出（不丢弃）
   4. join 所有工作线程（等待完成）
   ```

3. **连接层优雅关闭**：
   - 停止 `accept` 新连接（关闭监听 socket 或从 epoll 中移除）。
   - 向现有连接发送"关闭通知"（应用层协议信号，如 HTTP `Connection: close` / gRPC `GOAWAY`）。
   - 等待现有请求处理完毕（设置超时，如 30s）。
   - 超时后强制 `close(fd)` 所有连接。

4. **信号处理**：
   - 注册 `SIGTERM` / `SIGINT` 处理函数，触发关闭流程（通过 `pipe` 或 `eventfd` 通知事件循环，避免在信号处理函数中做非 async-signal-safe 操作）。
   - Kubernetes 默认发送 `SIGTERM`，等待 `terminationGracePeriodSeconds`（默认 30s）后发 `SIGKILL`。

5. **排空顺序**（从外到内）：
   ```
   停止接受新连接 → 处理完现有请求 → 提交完当前 DB/消息队列事务 → 停止线程池 → 关闭存储连接 → 退出进程
   ```

6. **Kubernetes / 容器部署的特殊处理**：
   - 先从 Endpoints 摘除 Pod（更新 Service）后再关闭，防止新流量路由到正在关闭的实例。
   - 但 kube-proxy 更新 iptables 有延迟（秒级），实践中在处理 `SIGTERM` 后 sleep 几秒再停止 accept，以吸收延迟窗口内的流量。

### 常见误区

- `stopped = true` 后立即 `detach` 线程（而非 `join`），导致线程未完成即析构线程池对象，产生 UAF。
- 忽略已入队任务，直接清空队列退出，丢失数据。
- 信号处理函数中直接操作 `std::mutex`（非 async-signal-safe），导致死锁。
- 关闭顺序错误：先关闭存储连接，再处理请求，导致请求处理失败。

### 加分点

- 提到 **`std::stop_token`（C++20）**：`std::jthread` 内置取消令牌，可优雅取消等待中的操作，比手动 `stopped` 标志更标准。
- 提到 **双阶段关闭**：第一阶段停止接受新请求（Draining），第二阶段等待 in-flight 请求超时（Quiescing），两个阶段独立计时。
- 提到 **`SO_LINGER`**：设置 `l_onoff=1, l_linger=0` 使 `close()` 发送 RST 而非 FIN，快速回收连接（适合强制关闭，但会导致客户端收到 connection reset，应仅作为最后手段）。

### 代码示例

```cpp
class ThreadPool {
    std::vector<std::thread> workers_;
    std::queue<std::function<void()>> tasks_;
    std::mutex mtx_;
    std::condition_variable cv_;
    std::atomic<bool> stopped_{false};

public:
    ~ThreadPool() { shutdown(); }

    void shutdown() {
        {
            std::lock_guard lock(mtx_);
            stopped_.store(true, std::memory_order_release);
        }
        cv_.notify_all();  // 唤醒所有等待线程

        for (auto& t : workers_) {
            if (t.joinable()) t.join();  // 等待所有线程完成已入队任务
        }
    }

    // 工作线程循环
    void worker_loop() {
        while (true) {
            std::function<void()> task;
            {
                std::unique_lock lock(mtx_);
                cv_.wait(lock, [&]{
                    return !tasks_.empty() || stopped_.load(std::memory_order_acquire);
                });
                if (tasks_.empty()) return;  // stopped 且队列空，退出
                task = std::move(tasks_.front());
                tasks_.pop();
            }
            task();  // 在锁外执行任务
        }
    }
};

// 信号处理（async-signal-safe 方式）
int g_shutdown_pipe[2];

void sig_handler(int) {
    char c = 1;
    write(g_shutdown_pipe[1], &c, 1);  // 仅用 write，async-signal-safe
}

// 主事件循环监听 g_shutdown_pipe[0] 的可读事件，触发优雅关闭
```

---

## 总结对比表

| 题目 | 核心关键词 | 高频考点 |
|------|-----------|---------|
| B1 线程池核心数 | CPU/IO密集型公式、压测验证、NUMA | 中频 |
| B2 Work-stealing | Chase-Lev deque、LIFO局部性、TBB | 中频 |
| B3 异步任务链 | future/promise缺陷、C++20协程、P2300 | 中频 |
| B4 定时器调度 | 时间轮 O(1)、timerfd+epoll、CLOCK_MONOTONIC | 中频 |
| B5 读写锁 | shared_mutex、写者饥饿、RCU、分片锁 | 中高频 |
| B6 虚假唤醒 | while/谓词、notify_one vs all、atomic::wait | 高频 |
| B7 SPSC ring buffer | memory_order、alignas(64)防false sharing | 高频（N2手撕关联） |
| B8 C++20协程 ⭐ | 调度器设计、io_uring集成、结构化并发 | 高频 |
| B9 thread_local | 线程池复用陷阱、析构顺序、TLS模型 | 中频 |
| B10 优雅关闭 | 排空任务、join、信号处理、K8s SIGTERM | 高频 |
