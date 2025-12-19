# 一些C++的总结
---
## 1.为什么析构函数要用 virtual？
* 原因：保证通过基类指针删除派生类对象时，派生类析构函数能够被调用。
* 示例：
```cpp
class Base {
public:
    virtual ~Base() { std::cout << "Base dtor\n"; }
};

class Derived : public Base {
public:
    ~Derived() { std::cout << "Derived dtor\n"; }
};

Base* p = new Derived();
delete p; // 调用顺序: Derived -> Base
```

* 如果非 virtual：只会调用基类析构函数，派生类资源泄漏（内存 / 文件 /锁 等）。

* 面试重点：

    * 用指针/引用多态时要 virtual

    * 类是“最终类”（final）可以不用 virtual
        * 原因：
            * virtual 会在对象中增加 vptr（虚表指针），占用额外内存（通常 8 字节）
            * 析构函数调用时多了间接跳转，微小性能开销
            * 如果没有派生类，就不会发生多态删除问题

---

## 2.什么是对象切片？什么时候会发生？
* 对象切片：当将派生类对象赋值给基类对象（按值传递）时，派生类部分会“丢失”。
* 示例：
```cpp
class Base { int a; };
class Derived : public Base { int b; };

Derived d;
Base b = d; // 派生类部分 b 被切掉，只保留 Base 部分
```
* 发生场景：
    * 按值赋值给基类
    * 容器存储基类对象，而插入的是派生类对象
* 面试提示：通常用指针或引用避免对象切片。

---

## 3.Rule of 3 / 5 / 0 是什么？举例说明。
* Rule of 3：如果类定义了 析构函数、拷贝构造函数、拷贝赋值运算符 中任意一个，就应该定义三个。
* Rule of 5（C++11）：在 Rule of 3 的基础上加上 移动构造函数和移动赋值运算符。
* Rule of 0：如果尽量使用 RAII / 智能指针，避免手动管理资源，可以什么都不写，让编译器生成默认函数。
```cpp
class RuleOf3 {
    int* data;
public:
    RuleOf3(int val) : data(new int(val)) {}
    ~RuleOf3() { delete data; }
    RuleOf3(const RuleOf3& other) { data = new int(*other.data); }
    RuleOf3& operator=(const RuleOf3& other) {
        if (this != &other) {
            delete data;
            data = new int(*other.data);
        }
        return *this;
    }
};
```
---

## 4.什么是RAII，RAII的本质
RAII（Resource Acquisition Is Initialization，资源获取即初始化）

### 什么是 RAII？
在 C++ 中，资源不仅指内存，还包括文件句柄、网络连接、互斥锁（Mutex）等一切“用完必须归还”的东西。RAII 的操作流程如下：
* 获取资源（构造）： 在对象的构造函数中请求资源（例如打开一个文件或申请一块内存）。
* 持有资源： 资源在对象的整个生命周期内保持有效。
* 释放资源（析构）： 在对象的析构函数中释放资源。
由于 C++ 保证在局部对象超出作用域（Scope）时会自动调用析构函数，因此无论程序是因为正常结束还是因为抛出异常退出，资源都会被百分之百安全地释放。

### RAII 的本质
如果用一句话概括 RAII 的本质，那就是：将资源的生命周期与局部对象的生命周期绑定（Binding）。
这种绑定带来了两个决定性的优势：
* 确定性（Determinism）： 与 Java 或 Python 的垃圾回收（GC）不同，RAII 确切地知道资源何时被释放——就在对象销毁的那一刻。这对于管理非内存资源（如锁和文件）至关重要。
* 异常安全（Exception Safety）： 如果函数在执行中突然抛出异常，普通的释放语句（如 fclose）可能会被跳过，但 RAII 对象的析构函数依然会被编译器自动调用。

### 常见的 RAII 例子
你可以看看在 C++ 标准库中，RAII 是如何随处可见的：

| 资源类型 | RAII 封装类 |
|---------|------------|
| 堆内存  | `std::unique_ptr`, `std::vector`, `std::string` |
| 互斥锁  | `std::lock_guard`, `std::unique_lock` |
| 文件流  | `std::fstream` |
| 线程    | `std::jthread` (C++20) |

---

## 5.智能指针
### 5.1 unique_ptr
独占资源，不可拷贝，只能通过 std::move 转移
```cpp
void processData(std::unique_ptr<int> ptr) {
    // 处理数据...
}

int main() {
    auto myData = std::make_unique<int>(42);
    // 使用std::move转移所有权
    processData(std::move(myData));
}
```
所有权逻辑：按值传递会夺取所有权，按引用或 get() 只是借用
```cpp
void processData(int* val) { // 或者 int& val
    if (val) {
        std::cout << "处理数据: " << *val << std::endl;
    }
}

int main() {
    auto myData = std::make_unique<int>(42);
    // 使用 .get() 获取原始指针，但不转移所有权
    processData(myData.get()); 
    // main 函数依然拥有 myData
}
```

### 5.2 shared_ptr
本质是共享所有权。它内部维护了一个计数器：
```cpp
#include <iostream>
#include <memory>

int main() {
    // 使用 make_shared 创建，比直接 new 更高效
    std::shared_ptr<int> p1 = std::make_shared<int>(100);
    
    {
        std::shared_ptr<int> p2 = p1; // 允许拷贝！计数器变为 2
        std::cout << "计数器: " << p1.use_count() << std::endl; 
    } // p2 超出作用域，计数器变回 1

    std::cout << "p2 销毁后计数器: " << p1.use_count() << std::endl;
    return 0;
} // p1 销毁，计数器归 0，内存释放
```
现代C++中常见的shared_ptr的用法
```cpp
struct DataBuffer {
    std::vector<uint8_t> data;
    // ... 其他信息
};

// 1. 数据读取模块创建一个共享对象
std::shared_ptr<DataBuffer> frame = std::make_shared<DataBuffer>();

// 2. 传递给视频模块（拷贝 shared_ptr，计数 +1）
videoThread.process(frame);

// 3. 传递给音频模块（再次拷贝，计数 +1）
audioThread.process(frame);

// 此时计数器为 3（main + video + audio）
// 无论哪个线程先执行完，计数器都会自动减 1。
// 只有当所有线程都不再持有这个 'frame' 时，DataBuffer 才会自动 free。
```
shared_ptr 的引用计数操作是线程安全的，但对象本身的访问不是, 我们可以把 shared_ptr 拆解为两部分来看：
* 控制块（Control Block）：存放引用计数的地方。它是原子更新的（类似于 C11 里的 atomic_fetch_add）。
* 原始指针（Raw Pointer）：指向你实际的数据。

场景 A：多个线程增加/减少引用计数（安全 ✅）

场景 B：多个线程通过指针读写同一个对象（不安全 ❌）

场景 C：多个线程修改 shared_ptr 指向哪里（不安全 ❌）

---

### 5.3 weak_ptr
就像是一个“旁观者”。它指向 shared_ptr 管理的对象，但不增加引用计数

如果一个线程正在销毁对象，另一个线程却试图访问它怎么办？这正是 std::weak_ptr 在多线程里的神技。

演示场景：
* 线程 A 持有 shared_ptr，并在处理完后准备释放。
* 线程 B 持有 weak_ptr，它想知道对象还在不在

```cpp
// 线程 B 的逻辑
if (std::shared_ptr<Data> shared_p = weak_p.lock()) { // 尝试“提升”为 shared_ptr
    // 提升成功！
    // 此时引用计数已加 1，对象保证在 shared_p 销毁前不会被线程 A 删掉
    shared_p->do_something();
} else {
    // 提升失败，说明对象已经被线程 A 释放了
    std::cout << "对象已消失，安全退出" << std::endl;
}
```
lock() 方法是原子的。它要么让你成功拿到一个合法的 shared_ptr（并增加计数），要么返回空。这完美解决了“检查指针是否有效”和“使用指针”之间的竞态问题

---

## 6 性能飞跃 —— 移动语义（Move Semantics）
移动语义的本质： 不是拷贝数据，而是 **“偷取”** 资源。 想象你搬家时，不是把旧房子的家具照样买一份新的放进新房子（拷贝），而是直接把旧房子的钥匙换成新房子的钥匙（移动）
```cpp
class BigBuffer {
    int* data;
    size_t size;
public:
    // 传统的拷贝构造函数 (C++98)
    BigBuffer(const BigBuffer& other) {
        data = new int[other.size];
        std::copy(other.data, other.data + other.size, data); // 耗时的拷贝
    }

    // 现代 C++ 移动构造函数 (C++11)
    BigBuffer(BigBuffer&& other) noexcept {
        data = other.data;  // 直接接管对方的指针
        size = other.size;
        other.data = nullptr; // 把对方置空，防止对方析构时 free 掉我的内存
        other.size = 0;
    }
};
```
本质区别：
* 拷贝：分配新内存 + 复制内容。
* 移动：只复制指针地址 + 原指针置空。效率提升了几个数量级！

std::move 的唯一作用就是：强制把一个“左值”转换成“右值”，从而触发这个类的移动构造函数，而不是拷贝构造函数

进阶：返回值优化 (RVO)

```cpp
std::vector<int> makeVector() {
    std::vector<int> v = {1, 2, 3};
    return v; // ❌ 不要写成 return std::move(v);
}
```
编译器有一种黑科技叫 RVO (Return Value Optimization)。它甚至连“移动”都省了，直接在函数外部接收者的内存空间里构造这个对象。如果你加了 std::move，反而会强迫编译器执行移动语义，干扰了更高级的 RVO 优化

完美转发：std::forward
* 在现代 C++ 中，一旦右值被命名，它就变成了左值

```cpp
template<typename T>
void relay(T&& arg) {
    // std::forward 会根据 T 的类型决定是否转换为右值
    target(std::forward<T>(arg)); 
}
```
实现一个简易的 SmartBuffer类和“五大函数”（The Rule of Five）
```cpp
#include <iostream>
#include <algorithm>

class SmartBuffer {
private:
    int* data;
    size_t size;

public:
    // 1. 构造函数 (Resource Acquisition)
    explicit SmartBuffer(size_t s) : size(s), data(new int[s]) {
        std::cout << "Allocated " << size << " integers.\n";
    }

    // 2. 析构函数 (Resource Release)
    ~SmartBuffer() {
        delete[] data;
        std::cout << "Resource freed.\n";
    }

    // 3. 拷贝构造函数 (Deep Copy)
    SmartBuffer(const SmartBuffer& other) : size(other.size), data(new int[other.size]) {
        std::copy(other.data, other.data + size, data);
        std::cout << "Deep copied.\n";
    }

    // 4. 移动构造函数 (Transfer Ownership) - 核心！
    SmartBuffer(SmartBuffer&& other) noexcept : data(other.data), size(other.size) {
        other.data = nullptr; // 偷走资源后，将原主置空
        other.size = 0;
        std::cout << "Moved.\n";
    }

    // 5. 移动赋值运算符
    SmartBuffer& operator=(SmartBuffer&& other) noexcept {
        if (this != &other) {
            delete[] data;      // 释放自己的旧资源
            data = other.data;  // 接管新资源
            size = other.size;
            other.data = nullptr;
            other.size = 0;
        }
        return *this;
    }
};
```
关键点解析：
* noexcept 的重要性：在移动构造函数后面加上 noexcept 是非常关键的。它告诉 STL 容器（如 std::vector）：“我的移动操作是绝对安全的，不会抛出异常。” 这样当 vector 扩容时，它才会放心使用高效的移动而不是耗时的拷贝
* 防止“自杀”：在赋值运算符中检查 this != &other，是为了防止你写出 a = std::move(a) 这种把自己资源先删了再赋值的情况
* 置空原对象：这是 C 程序员最容易忘记的一步。如果你不把 other.data 置为 nullptr，那么当 other 超出作用域执行析构函数时，它会 delete[] 掉你刚刚“偷”过来的内存

---

## 7 STL
### 7.1 std::vector
std::vector 扩容
* 申请空间：通常按 1.5 倍或 2 倍增长。
* 元素迁移：这是关键！它不是简单的 memcpy。
    * 如果元素定义了 noexcept 移动构造函数，vector 会调用移动构造函数把元素“偷”到新家。
    * 如果没有移动构造函数，它会退而求其次调用拷贝构造函数（虽然慢，但保证安全）。
* 销毁旧家：调用旧位置元素的析构函数，并释放旧内存。

核心优化技术：reserve() 与 emplace_back()
* 避免频繁扩容：reserve()
```cpp
std::vector<int> v;
v.reserve(1000); // 直接分配 1000 个人的座位，但还没坐人
```
* 避免临时对象：emplace_back()
```cpp
struct Point {
    Point(int x, int y) {}
};

std::vector<Point> v;
v.push_back(Point(1, 2));  // 1. 创建临时对象 2. 拷贝/移动进 vector 3. 销毁临时对象
v.emplace_back(1, 2);      // 直接在 vector 内存里构造 Point，0 次拷贝！
```

扩容对指针的影响
这是 C 程序员最容易掉进去的坑——迭代器失效（Iterator Invalidation）。

```cpp
std::vector<int> v = {1, 2, 3};
int* p = &v[0]; // 拿到第一个元素的地址

v.push_back(4); 
v.push_back(5); // 假设这里触发了扩容（重新分配内存）

// 💣 危险！此时 p 指向的是已经被释放的旧内存！
// std::cout << *p << std::endl;
```
在 C 语言中，如果你 realloc 了指针，原指针也可能失效。在 C++ 中，这种风险同样存在。

**重点： 如果你把 SmartBuffer 移动构造函数后的 noexcept 删掉，再运行程序，你会发现 vector 扩容时变怂了——它会改用拷贝。这就是 C++ 的“异常安全保证”**

---

### 7.2 std::unordered_map
基于 **哈希表** 实现的，提供了平均时间复杂度为 $O(1)$ 的查找、插入和删除效率。
底层结构：桶（Buckets）
std::unordered_map 的底层通常是一个数组，每个元素被称为一个 **“桶（Bucket）”**。
* 冲突处理：当两个不同的键产生相同的哈希值时，C++ 标准库默认使用链地址法（即每个桶后面挂一个链表）
* 负载因子（Load Factor）：当元素数量与桶数量的比值超过某个阈值（默认是 1.0）时，它会自动触发 Rehash（重新哈希），即申请更大的内存并重新分布所有元素

C 程序员必知的性能陷阱
虽然 unordered_map 很好用，但相比于 vector，它有几个明显的开销：
* 内存不连续：由于使用了链地址法，节点在内存中是分散的，这对 CPU 缓存（Cache）不友好。如果你的数据量很小（比如只有 10 个元素），用 vector 配合 std::find 往往比 unordered_map 更快。

* 哈希开销：每次插入或查找都要计算哈希值。如果你的键是很长的字符串，计算哈希可能比比较字符串更耗时。

优化建议： 如果你预先知道要存多少数据，同样可以使用 reserve() 来避免多次 Rehash。

什么时候用 std::map vs std::unordered_map？

| 特性 | std::map | std::unordered_map |
| ------ | ---- | --------- |
| 底层实现 | 红黑树 (平衡二叉树) | 哈希表 |
| 查找复杂度 | $O(\log n)$ | 平均 $O(1)$，最坏 $O(n)$
| 元素顺序	| 有序（按 Key 排序） |	无序 |
| 适用场景	| 需要按顺序遍历，或需要范围查询  |	只追求最快的查找速度 |

键值（Key）是如何变成哈希值的？
哈希值的生成是由 std::hash<Key> 这个仿函数完成的。对于不同的数据类型，它的策略不同：

A. 整数类型：简单直接
对于 int、long 等类型，std::hash 通常直接返回原值（或者强转为 size_t）。
* 原因：整数本身已经分布得很好了，没必要再浪费 CPU 去算复杂的算法。

B. 字符串类型：核心算法
对于 std::string，不能直接用地址，必须根据内容计算。目前主流编译器（GCC, Clang）通常使用 MurmurHash 或 FNV-1a。

以 FNV-1a 为例，它的 C 伪代码极其简单高效：
```cpp
uint64_t fnv1a_hash(const char* str) {
    uint64_t hash = 0xcbf29ce484222325; // 初始偏移量（Offset Basis）
    while (*str) {
        hash ^= (uint8_t)*str++;       // 异或当前字符
        hash *= 0x100000001b3;         // 乘以一个巨大的质数（FNV prime）
    }
    return hash;
}
```

通过不断的异或和质数乘法，哪怕字符串只差一个字母，最终产生的 size_t 结果也会天差地别。

为什么会产生相同的哈希值?
第一层：哈希值碰撞（数学上的必然）
* 原理：size_t 在 64 位系统下虽然很大（$2^{64}$），但它是有限的。而理论上 Key 的组合是无限的（比如你可以写无限长的字符串）。
* 结论：根据“鸽巢原理”，必然存在两个不同的 Key 指向同一个 size_t 数值。只不过在好的算法下，这种概率极低。

第二层：索引碰撞（工程上的必然）这是最常见的冲突。虽然 hash("Alice") 和 hash("Bob") 产生的 size_t 不同，但 unordered_map 内部的 **桶** 数量是有限的（比如初始只有 13 个桶）。
* 计算索引：index = hash_value % bucket_count;
* 冲突发生：
    * hash("Alice") = 1234567 -> 1234567 % 13 = 5
    * hash("Bob")   = 9876548 -> 9876548 % 13 = 5

* 结果：即便哈希值本身不同，但取模后它们都要挤进 5 号桶。

C++ 如何处理这些碰撞？
当两个 Key 掉进同一个桶时，std::unordered_map 默认使用 链地址法（Chaining）：

* 每个桶（Bucket）本质上是一个链表的头指针。
* 当 "Alice" 和 "Bob" 都映射到 5 号桶时，5 号桶的链表里就会有两个节点。
* 查找过程：
    * 先算哈希，找到 5 号桶。
    * 遍历 5 号桶的链表。
    * 关键点：此时会调用 Key 的 operator==。这就是为什么自定义类型作为 Key 时，既要提供哈希函数，又要重载 == 的原因。

性能的关键：负载因子（Load Factor）

如果桶很少，数据很多，链表就会变得很长，$O(1)$ 的查找就会退化成 $O(n)$。
* 负载因子 = 元素总数 / 桶数。
* Rehash：当负载因子超过阈值（通常是 1.0）时，unordered_map 会自动申请更多的桶（通常翻倍），并把所有元素重新分配一遍。这就像 C 语言里的 realloc，非常耗时。

C 程序员的优化直觉：如果你知道要存 1000 个元素，提前调用 my_map.reserve(1000);。这会一次性分配好足够的桶，避免多次 Rehash 带来的性能抖动。

---

## 8 Lambda
Lambda 的本质：闭包与捕获

Lambda 最强大的地方在于它能**“捕获”**所在作用域内的变量。这在 C 语言的普通函数指针中是做不到的。

有三种常见的捕获方式：
* [val]（按值捕获）：拷贝一份变量。即便原变量改了，Lambda 内部的副本不变。
* [&ref]（按引用捕获）：直接操作原变量。注意： 这有 RAII 风险，如果 Lambda 存活时间比变量长，会引用失效。
* [=] 或 [&]：自动捕获作用域内用到的所有变量（全按值或全按引用）。

Lambda 的底层原理（面试常考）

作为 C 程序员，你可以把 Lambda 理解为一个由编译器自动生成的匿名结构体（仿函数）。

当你写下 [threshold](int dist) { ... } 时，编译器实际上生成了类似这样的东西：

```cpp
struct AnonymousLambda {
    int threshold; // 捕获的变量变成了成员变量
    AnonymousLambda(int t) : threshold(t) {}
    
    bool operator()(int dist) const { // 重载了 () 运算符
        return dist < threshold;
    }
};
```
**为什么 Lambda 比函数指针快？** 因为编译器知道 Lambda 的确切代码，可以直接进行**内联（Inline）**优化，而函数指针通常需要通过地址进行间接调用，难以优化。

---

## 9 std::string
std::string 是 RAII 封装的、可变长的、拥有所有权的字符序列容器
* 管理一段 连续的字符内存
* 自动申请 / 释放（不用你 new/delete）
* 可以动态增长
* 行为像值类型（能拷贝、能移动）

小字符串优化（SSO）

👉 不会分配堆内存！
* 短字符串直接存在 std::string 对象内部
* 减少 malloc/free
* 拷贝更快，cache 友好

📌 常见阈值：
* 15 字节（64 位平台，libstdc++）

性能 & 最佳实践

只读参数用 std::string_view
```cpp
void foo(std::string_view sv);
```
* 不拷贝
* 不分配
* 特别适合解析、协议、日志

**注意生命周期**
```cpp
std::string_view sv = std::string("abc"); // ❌ 悬空
```

构造前 reserve
```cpp
std::string s;
s.reserve(n);
```

返回 std::string 没问题
```cpp
std::string make() {
    return "hello";
}
```
* NRVO / move
* 不用担心性能

⭐ 面试总结金句

* std::string 是连续内存、RAII 管理的值类型；
* 拷贝是深拷贝，移动是资源转移；
* 性能敏感的只读场景优先使用 std::string_view，
* 注意内存重分配会使指针和引用失效。
