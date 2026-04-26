# 核心要点
### 1.读写指针用 std::atomic<size_t>，保证跨线程可见性
### 2.内存序配对关系（形成 release-acquire 同步链）：
* 生产者：写入数据 → write_ptr.store(release) ← 把数据"发布"出去
* 消费者：write_ptr.load(acquire) → 读取数据 ← acquire 保证能看到 release 之前的所有写入
* 消费者：消费数据 → read_ptr.store(release)
* 生产者：read_ptr.load(acquire) ← 判断非满
### 3.relaxed 写 + acquire 读不构成同步：acquire 只与同一变量上的 release 配对，数据写入不被保护，ARM 上 store-store reorder 会导致消费者读到未初始化的 buffer 内容

```cpp
#include <atomic>
#include <array>
#include <optional>

template<typename T, size_t N>
class SPSCQueue {
    static_assert((N & (N - 1)) == 0, "N must be power of 2");

    std::array<T, N> buffer_{};
    alignas(64) std::atomic<size_t> write_pos_{0};  // 生产者独占写
    alignas(64) std::atomic<size_t> read_pos_{0};   // 消费者独占写

public:
    // 生产者调用
    bool push(const T& val) {
        const size_t w = write_pos_.load(std::memory_order_relaxed); // 自己写的，relaxed即可
        const size_t next_w = (w + 1) & (N - 1);

        // acquire 读对端指针：确保看到消费者最新消费进度
        if (next_w == read_pos_.load(std::memory_order_acquire))
            return false; // full

        buffer_[w] = val;

        // release：数据写入 happens-before 此 store，消费者 acquire 后可见
        write_pos_.store(next_w, std::memory_order_release);
        return true;
    }

    // 消费者调用
    std::optional<T> pop() {
        const size_t r = read_pos_.load(std::memory_order_relaxed); // 自己写的，relaxed即可

        // acquire 读对端指针：确保看到生产者写入 buffer 的内容
        if (r == write_pos_.load(std::memory_order_acquire))
            return std::nullopt; // empty

        T val = buffer_[r];

        // release：数据消费 happens-before 此 store，生产者 acquire 后可见
        read_pos_.store((r + 1) & (N - 1), std::memory_order_release);
        return val;
    }
};
```
# 常见误区
|误区	|后果 |
| ------ | ------ |
|读写指针不加 alignas(64) 隔离	|False Sharing：两个指针在同一 cache line，生产/消费互相触发 cache invalidation |
|全部用 seq_cst	|不必要的全局 fence，在 ARM 上性能损耗显著 |
|用指针大小不是 2 的幂	|取模用 % 而非 &，触发除法指令 |
|push/pop 各自的 load 也用 acquire	|读自己写的指针不需要 acquire，浪费 barrier |

# 加分点
* alignas(64) 防 False Sharing：write_pos_ 和 read_pos_ 必须分布在不同 cache line，否则 SPSC 会退化成两核之间的乒乓效应，延迟大幅上升
* 容量必须是 2 的幂：用位运算 & (N-1) 代替取模，避免除法
* x86 的 TSO（Total Store Order）内存模型天然防止 store-store reorder，所以 relaxed store 在 x86 上碰巧不出问题——这是 架构依赖的"幸运"，不是正确性保证