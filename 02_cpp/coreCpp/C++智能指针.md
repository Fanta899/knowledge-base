# 1 unique_ptr
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

# 2 shared_ptr
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

# 3 weak_ptr
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
