# C++ 高级面试知识点笔记

> 覆盖范围：语言特性 · 系统设计 · 算法 · 并发 · 模板
> C++ 标准：C++17/20/23

---

## 目录

1. [移动语义与 std::move](#1-移动语义与-stdmove)
2. [shared_ptr 内部实现](#2-shared_ptr-内部实现)
3. [内存顺序 memory_order](#3-内存顺序-memory_order)
4. [异步日志系统设计](#4-异步日志系统设计)
5. [容器迭代器失效与 erase](#5-容器迭代器失效与-erase)
6. [noexcept 与 vector 扩容](#6-noexcept-与-vector-扩容)
7. [完美转发与引用折叠](#7-完美转发与引用折叠)
8. [虚函数机制 vtable / vptr](#8-虚函数机制-vtable--vptr)
9. [CRTP 奇异递归模板模式](#9-crtp-奇异递归模板模式)
10. [C++20 Concepts](#10-c20-concepts)
11. [False Sharing 与缓存优化](#11-false-sharing-与缓存优化)

---

## 1. 移动语义与 std::move

### std::move 的本质

`std::move` 是纯粹的**类型转换**，不移动任何数据，等价于：

```cpp
static_cast<std::remove_reference_t<T>&&>(t)
```

它将值转换为**右值引用（`T&&`）**，本身零运行时开销。

### 为什么产生移动行为

编译器重载决议：看到右值引用实参时，优先匹配接受 `T&&` 的移动构造/移动赋值，而非 `const T&` 的拷贝版本。`std::move` 只是给编译器一个"信号"。

### 被移动对象的状态

标准规定：**valid but unspecified state（有效但未指定状态）**

- 对象仍然存活，析构函数可以安全调用
- 内部值不确定，不能读取或依赖
- 可以重新赋值后继续使用

```cpp
std::string s = "hello";
std::string t = std::move(s);
// s 处于 valid but unspecified 状态
s = "world";  // OK，重新赋值后可用
```

### 常见误区

| 误区 | 正确认知 |
|------|---------|
| `std::move` 本身执行数据移动 | 只做类型转换，移动发生在移动构造/赋值函数中 |
| 被移动对象不能再使用 | 可析构和重新赋值，但不能读取其值 |
| `const` 对象 `std::move` 会触发移动 | 得到 `const T&&`，退化为拷贝 |

### 加分点

```cpp
// 具名右值引用在函数体内是左值，需再次 move
void foo(std::string&& s) {
    bar(s);             // 调用拷贝
    bar(std::move(s));  // 调用移动 ✅
}

// 不要对返回值手动 move，会阻止 RVO
std::string make() {
    std::string s = "hello";
    return s;           // ✅ 触发 NRVO
    // return std::move(s); // ❌ 阻止 NRVO，可能更慢
}
```

> **noexcept 的重要性**：移动构造函数应标记 `noexcept`，否则 `std::vector` 扩容时会退回使用拷贝（为保证强异常安全）。

---

## 2. shared_ptr 内部实现

### 内部结构

每个 `shared_ptr` 持有两个指针：
- 指向**被管理对象**的指针
- 指向**控制块（control block）** 的指针

```
shared_ptr<T>             控制块
┌─────────────┐          ┌──────────────────────┐
│  ptr ───────┼─────────►│  managed object      │ ← make_shared 时内嵌
│  ctrl ──────┼─────────►│  use_count (atomic)  │
└─────────────┘          │  weak_count (atomic) │
                         │  deleter             │
                         │  allocator           │
                         └──────────────────────┘
```

控制块在堆上分配，被所有共享同一对象的 `shared_ptr` 共同指向。

### 引用计数

- `use_count`：强引用计数，`atomic<int>`
- `weak_count`：弱引用计数（`weak_ptr` 数量 + 1），`atomic<int>`
- `use_count == 0`：释放被管理对象
- `weak_count == 0`：释放控制块本身

### 线程安全边界

| 操作 | 线程安全？ |
|------|-----------|
| 多线程各自拷贝/销毁不同 `shared_ptr`，指向同一对象 | ✅ 安全 |
| 多线程并发读同一个 `shared_ptr` 对象 | ✅ 安全 |
| 多线程并发读写同一个 `shared_ptr` 对象（如赋值） | ❌ UB |

```cpp
// 正确做法：C++20 atomic shared_ptr
std::atomic<std::shared_ptr<int>> p = std::make_shared<int>(42);

// 线程 A
p.store(std::make_shared<int>(100));

// 线程 B
auto local = p.load();
std::cout << *local << std::endl;
```

### make_shared vs shared_ptr(new T)

| | `make_shared` | `shared_ptr(new T)` |
|---|---|---|
| 内存分配次数 | 1次（对象+控制块合并） | 2次 |
| 性能 | 更好 | 较差 |
| 异常安全 | 更安全 | 裸 new 可能泄漏 |
| 缺点 | 对象和控制块生命周期绑定（weak_ptr 存在时内存延迟释放） | 无此问题 |

---

## 3. 内存顺序 memory_order

### 各级别含义

| 内存序 | 含义 |
|--------|------|
| `relaxed` | 只保证原子性，不提供跨线程顺序约束 |
| `acquire` | 本操作之后的读写，不能重排到本操作之前 |
| `release` | 本操作之前的读写，不能重排到本操作之后 |
| `seq_cst` | 所有 seq_cst 操作全局上有单一总顺序，最强语义 |

### acquire/release 同步模型

```cpp
int data = 0;
std::atomic<bool> ready{false};

// 线程 A（发布者）
data = 42;
ready.store(true, std::memory_order_release);  // release 前的写不能跑到后面

// 线程 B（订阅者）
if (ready.load(std::memory_order_acquire)) {   // acquire 后的读不能跑到前面
    std::cout << data;  // 保证看到 42
}
```

`acquire` 读到 `release` 写入的值时，建立 `synchronizes-with` 关系，形成 happens-before 保证。

### 使用场景选择

| 场景 | 推荐内存序 |
|------|-----------|
| 纯计数器、统计、不依赖时序 | `relaxed` |
| 发布-订阅、标志位同步 | `release` / `acquire` |
| 需要全局一致总顺序 | `seq_cst` |

### 常见误区

- `relaxed` 不是"完全随机"，同一原子变量的修改顺序仍然存在
- 原子变量不能自动保护关联的普通变量，需要通过 acquire/release 建立同步边
- `memory_order_consume` 在实践中基本按 acquire 处理，不建议使用

---

## 4. 异步日志系统设计

### 整体架构

**多生产者单消费者（MPSC）有界环形缓冲区**

```
业务线程1 ─┐
业务线程2 ─┼──► [MPSC Ring Buffer] ──► 消费者线程 ──► 磁盘
业务线程N ─┘
```

### MPSC 环形缓冲区核心协议

```cpp
struct Slot {
    std::atomic<uint64_t> sequence;
    LogRecord record;
};

bool tryPush(LogRecord rec) {
    const uint64_t ticket = tail.fetch_add(1, std::memory_order_relaxed);
    Slot& slot = ring[ticket % capacity];

    if (slot.sequence.load(std::memory_order_acquire) != ticket) {
        dropped.fetch_add(1, std::memory_order_relaxed);
        return false;  // 队列满，丢弃
    }

    slot.record = std::move(rec);
    slot.sequence.store(ticket + 1, std::memory_order_release);
    return true;
}

bool tryPop(LogRecord& out) {
    Slot& slot = ring[head % capacity];
    if (slot.sequence.load(std::memory_order_acquire) != head + 1) {
        return false;
    }
    out = std::move(slot.record);
    slot.sequence.store(head + capacity, std::memory_order_release);
    ++head;
    return true;
}
```

### 分级背压策略（满队列处理）

| 水位 | 策略 |
|------|------|
| < 70% | 正常接受所有日志 |
| > 70% | 开始丢弃 `DEBUG` |
| > 85% | 丢弃 `INFO` |
| > 95% | 只保留 `ERROR/FATAL` |

`ERROR/FATAL` 的 fallback：线程本地紧急缓冲 + 后台抢救刷盘，或降级同步写。

### 性能优化要点

- `head` 与 `tail` 分 cache line 放置，避免 False Sharing
- 消费者批量落盘（聚合到 `writev` 大块写）
- 后台线程定时 + 阈值双触发 flush
- 暴露可观测性指标：`dropped_count`、`queue_occupancy`、`flush_latency`

---

## 5. 容器迭代器失效与 erase

### 原始写法的问题

```cpp
// ❌ 错误：erase 后迭代器失效，++it 跳元素或 UB
for (auto it = v.begin(); it != v.end(); ++it) {
    if (*it % 2 == 0) {
        v.erase(it);
    }
}
```

**问题**：`erase` 使当前位置及之后的迭代器全部失效，再执行 `++it` 是未定义行为。

### 正确写法

```cpp
// 方式1：接收 erase 返回的有效迭代器
for (auto it = v.begin(); it != v.end(); ) {
    if (*it % 2 == 0) {
        it = v.erase(it);
    } else {
        ++it;
    }
}

// 方式2：现代 C++20 推荐写法
std::erase_if(v, [](int x) { return x % 2 == 0; });
```

### 复杂度对比

| 方式 | 时间复杂度 | 元素移动次数 | 缓存友好性 |
|------|-----------|-------------|-----------|
| 逐个 `erase` | O(n²) 最坏 | 反复搬移，接近二次方 | 差，反复 memmove |
| `erase_if`（erase-remove） | O(n) | 接近保留元素数量 | 好，线性扫描写回 |

### 顺序无关时的 O(1) 删除技巧

```cpp
// swap with back + pop_back，均摊 O(1)
void fastErase(std::vector<int>& v, size_t idx) {
    v[idx] = std::move(v.back());
    v.pop_back();
}
```

---

## 6. noexcept 与 vector 扩容

### 扩容策略决策树

```
vector 扩容，迁移元素时：
│
├── T 有 noexcept 移动构造？
│   └── YES → 使用移动构造 ✅（快速，strong guarantee 可维持）
│
└── NO（移动可能抛异常）
    ├── T 有拷贝构造？
    │   └── YES  → 使用拷贝构造（保证 strong exception guarantee）
    └── NO（只有可抛移动）
        └── 只能移动，strong guarantee 无法保证，降级为 basic guarantee
```

### 三种异常安全级别

| 级别 | 含义 |
|------|------|
| **Strong guarantee** | 操作失败后状态完全回滚，和操作前一致 |
| **Basic guarantee** | 操作失败后对象仍然有效可析构，但值可能改变 |
| **No-throw guarantee** | 操作保证不抛异常 |

### 工程实践

```cpp
struct MyType {
    MyType(MyType&& other) noexcept { ... }       // ✅ 声明 noexcept
    MyType& operator=(MyType&& other) noexcept { ... }  // ✅
};
```

> 标准库中 `std::vector`、`std::deque` 等容器在扩容时会通过 `std::is_nothrow_move_constructible_v<T>` 检测，动态选择移动还是拷贝路径。

---

## 7. 完美转发与引用折叠

### 万能引用 vs 右值引用

| | `int&&` | `T&&`（模板参数推导） |
|---|---|---|
| 类型 | 右值引用（固定） | 万能引用（universal reference） |
| 绑定左值 | ❌ | ✅ |
| 绑定右值 | ✅ | ✅ |

### 模板参数推导规则

```cpp
template <typename T>
void wrapper(T&& arg);

int x = 42;
wrapper(x);   // T 推导为 int&,  参数类型 int& && → 折叠为 int&
wrapper(42);  // T 推导为 int,   参数类型 int&&
```

### 引用折叠规则（四条规则）

| 原始类型 | 折叠结果 |
|----------|----------|
| `T& &`   | `T&`     |
| `T& &&`  | `T&`     |
| `T&& &`  | `T&`     |
| `T&& &&` | `T&&`    |

> 口诀：**有左值引用则结果为左值引用，全是右值引用才是右值引用。**

### std::forward 的本质

```cpp
template <typename T>
T&& forward(std::remove_reference_t<T>& arg) noexcept {
    return static_cast<T&&>(arg);
}
```

- `T = int`  → `static_cast<int&&>`  → 右值，触发移动
- `T = int&` → `static_cast<int& &&>` → 折叠为 `int&`，触发拷贝

**必须显式传 `T`**：函数参数 `arg` 在函数体内永远是左值，`forward` 必须靠显式传入的 `T` 恢复原始值类别。

### std::forward vs std::move

| | `std::move` | `std::forward<T>` |
|---|---|---|
| 作用 | 无条件转为右值引用 | 按 `T` 恢复原始值类别 |
| 需要显式参数 | 否 | 是 |
| 使用场景 | 主动放弃所有权 | 转发函数参数 |

### 转发包（Variadic Forwarding）

```cpp
template <typename... Args>
void wrapper(Args&&... args) {
    foo(std::forward<Args>(args)...);
}

// C++20 lambda 完美转发
auto wrapper = [](auto&& arg) {
    foo(std::forward<decltype(arg)>(arg));
};
```

---

## 8. 虚函数机制 vtable / vptr

### 核心结构

- **vtable**：每个**类**一份，存放在程序只读数据段（`.rodata`），包含虚函数指针数组
- **vptr**：每个**对象**独有，一个指针（8字节，64位平台），指向该对象所属类的 vtable

```
对象内存布局：
┌──────────┐
│  vptr ───┼──► Base::vtable [ &Base::print, &Base::foo, ... ]
├──────────┤
│  data    │
└──────────┘
```

### vptr 初始化时机

**逐层构造，逐层更新**：

```
构造 Derived d：
  1. 进入 Base 构造函数    → vptr = &Base::vtable
  2. Base() 函数体执行     → 调用虚函数 → 解析到 Base::xxx
  3. Base 构造完成
  4. vptr 更新 = &Derived::vtable
  5. Derived 构造函数体执行 → 调用虚函数 → 解析到 Derived::xxx
```

### 构造函数中调用虚函数

```cpp
struct Base {
    Base() { print(); }           // 只输出 "Base"
    virtual void print() { std::cout << "Base\n"; }
};
struct Derived : Base {
    void print() override { std::cout << "Derived\n"; }
};

Derived d;  // 输出：Base（不是 Derived）
```

> C++ 标准规定：构造/析构期间的虚函数调用，解析到**当前正在构造/析构的类**，而非最终派生类。这是为了防止访问未初始化的派生类成员。

### 虚调用的性能影响

1. 两次间接寻址：`vptr → vtable → 函数地址`
2. 阻碍内联优化
3. 对热路径有影响，可考虑 CRTP 替代

---

## 9. CRTP 奇异递归模板模式

### 基本结构

```cpp
template <typename Derived>
struct Base {
    void interface() {
        // 编译期多态，无虚函数开销
        static_cast<Derived*>(this)->implementation();
    }
};

struct Concrete : Base<Concrete> {
    void implementation() {
        std::cout << "Concrete\n";
    }
};
```

### 虚函数 vs CRTP 对比

| | 虚函数 | CRTP |
|---|---|---|
| 多态时机 | 运行期 | 编译期 |
| vptr 开销 | 有（每对象 8 字节） | 无 |
| 内联优化 | 通常不可内联 | 可内联 |
| 存混合类型容器 | ✅ | ❌ |
| 适用场景 | 需要运行期动态分派 | 热路径、实时系统 |

### 误用防护

```cpp
// ❌ 危险写法：Wrong 继承 Base<Concrete>，类型不匹配 → UB
struct Wrong : Base<Concrete> { ... };

// ✅ static_assert 防护
template <typename Derived>
struct Base {
    void interface() {
        static_assert(std::is_base_of_v<Base, Derived>,
            "Derived must inherit from Base<Derived>");
        static_cast<Derived*>(this)->implementation();
    }
};
```

### C++20 替代方案：Deducing This

```cpp
struct Base {
    void interface(this auto&& self) {
        self.implementation();  // 直接推导，无需 static_cast
    }
};
```

### 常见用途

- Mixin 模式（注入通用行为）
- 比较运算符自动生成
- `std::enable_shared_from_this`（本质是 CRTP）

---

## 10. C++20 Concepts

### SFINAE vs Concepts

```cpp
// SFINAE 写法：报错信息难以理解，模板展开层层嵌套
template <typename T>
std::enable_if_t<std::is_integral_v<T>, T> add(T a, T b) {
    return a + b;
}

// Concepts 写法：报错直接指向约束不满足，清晰易读
template <std::integral T>
T add(T a, T b) {
    return a + b;
}
```

### requires 表达式四种形式

```cpp
template <typename T>
concept Example = requires(T a, T b) {
    // 1. 简单要求：表达式合法即可
    a + b;

    // 2. 类型要求：类型存在
    typename T::value_type;

    // 3. 复合要求：表达式合法 + 返回类型约束
    { a + b } -> std::convertible_to<T>;

    // 4. 嵌套要求：嵌套布尔条件
    requires std::is_integral_v<T>;
};
```

### 自定义 Concept 示例

```cpp
// 约束 T 支持 + 且结果可转换为 T
template <typename T>
concept Addable = requires(T a, T b) {
    { a + b } -> std::convertible_to<T>;
};

template <Addable T>
T add(T a, T b) { return a + b; }
```

### convertible_to vs same_as

| 约束 | 含义 |
|------|------|
| `-> std::same_as<T>` | 返回类型**精确等于** `T` |
| `-> std::convertible_to<T>` | 返回类型**可隐式转换为** `T`（更宽松）|

### requires 子句的三种等价写法

```cpp
// 写法1：模板参数位置
template <Addable T>
T add(T a, T b);

// 写法2：requires 子句
template <typename T> requires Addable<T>
T add(T a, T b);

// 写法3：尾置 requires
template <typename T>
T add(T a, T b) requires Addable<T>;
```

### Concepts 与重载决议

满足**更具体约束**的模板优先被选中：

```cpp
template <typename T>
void foo(T) { std::cout << "generic\n"; }

template <std::integral T>
void foo(T) { std::cout << "integral\n"; }

foo(42);    // 输出：integral（更具体的约束优先）
foo(3.14);  // 输出：generic
```

---

## 11. False Sharing 与缓存优化

### 什么是 False Sharing

- CPU cache 以 **cache line** 为最小单位（通常 **64 字节**）加载和失效
- 两个逻辑上独立的变量若落在同一 cache line，多核并发修改时互相使对方 cache line 失效
- 逻辑正确，但产生严重性能退化（**ping-pong 效应**）

```cpp
// ❌ False Sharing：countA 和 countB 极大概率在同一 cache line
struct Counters {
    std::atomic<int> countA{0};  // 4字节
    std::atomic<int> countB{0};  // 4字节，紧邻 countA
};
```

### MESI 缓存一致性协议

| 状态 | 含义 |
|------|------|
| **M** (Modified) | 本核独占，已修改，其他核无效 |
| **E** (Exclusive) | 本核独占，未修改 |
| **S** (Shared) | 多核共享，只读 |
| **I** (Invalid) | 无效，需重新加载 |

**False Sharing 发生过程：**

1. 核 A 修改 `countA` → 该 cache line 在核 A 变为 **M**，核 B 变为 **I**
2. 核 B 要写 `countB` → 发现 cache line **Invalid** → 必须重新加载
3. 核 B 修改后 → 核 A 的 cache line 又变 **I**
4. 两核不断互相使对方失效 → **ping-pong** → 性能暴跌

### 修复方案

```cpp
// 方案1：C++17 标准常量（推荐）
struct alignas(std::hardware_destructive_interference_size) PaddedCounter {
    std::atomic<int> value{0};
};

struct Counters {
    PaddedCounter countA;
    PaddedCounter countB;
};

// 方案2：手动 alignas(64)
struct Counters {
    alignas(64) std::atomic<int> countA{0};
    alignas(64) std::atomic<int> countB{0};
};
```

### 相关标准库常量

| 常量 | 用途 |
|------|------|
| `std::hardware_destructive_interference_size` | 避免 False Sharing，变量间最小隔离尺寸 |
| `std::hardware_constructive_interference_size` | 利用 True Sharing，热数据放同一 cache line 提升读性能 |

### SoA vs AoS

```cpp
// AoS（Array of Structures）：同对象字段连续，但遍历单字段时缓存利用率低
struct Particle { float x, y, z, mass; };
Particle particles[1000];

// SoA（Structure of Arrays）：同字段连续，SIMD 友好，遍历单字段缓存更高效
struct Particles {
    float x[1000], y[1000], z[1000], mass[1000];
};
```

### 检测工具

```bash
# Linux perf c2c：直接显示 cache line 争用热点
perf c2c record ./your_program
perf c2c report
```

---

## 总结对比速查表

### 异常安全级别

| 级别 | 含义 | 触发条件示例 |
|------|------|-------------|
| No-throw | 保证不抛异常 | `noexcept` 函数 |
| Strong | 失败后完全回滚 | 拷贝构造可用时的 vector 扩容 |
| Basic | 失败后对象仍有效 | 只有可抛移动构造时的 vector 扩容 |
| None | 无保证 | 裸指针操作 |

### 智能指针选择

| 场景 | 选择 |
|------|------|
| 独占所有权 | `std::unique_ptr` |
| 共享所有权 | `std::shared_ptr` |
| 打破循环引用 | `std::weak_ptr` |
| 多线程共享原子操作 | `std::atomic<std::shared_ptr<T>>` |

### 多态方式选择

| 场景 | 选择 |
|------|------|
| 运行期动态类型，存混合容器 | 虚函数（runtime 多态） |
| 编译期已知类型，热路径性能敏感 | CRTP（compile-time 多态） |
| 模板约束，替代 SFINAE | Concepts |
| C++23 简化 CRTP | Deducing This |

---

*整理自 C++ 高级工程师技术面试，C++17/20/23 标准*
