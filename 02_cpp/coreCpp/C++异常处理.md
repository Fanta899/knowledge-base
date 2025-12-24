# Try-Catch 与 Throw
C++ 允许你直接在错误发生地“扔出”一个炸弹，并让代码自动跳回能处理它的地方。
```cpp
void do_step3() {
    if (something_wrong) throw std::runtime_error("Disk Full"); 
}

void business_logic() {
    try {
        do_step1(); // 内部调用了 step2, step2 调用了 step3
    } catch (const std::exception& e) {
        std::cerr << "捕获到异常: " << e.what() << std::endl;
    }
}
```

**核心奥秘：栈解旋 (Stack Unwinding) —— 为什么它需要 RAII？**

作为 C 程序员，你一定会问：“如果我在 step2 里 malloc 了一块内存，还没来得及 free 就发生了 throw，那内存不就泄露了吗？”

**这正是 RAII存在的终极意义**

当异常发生时，C++ 运行时会执行栈解旋：它会沿着调用栈向上寻找 catch 块。在“逃离”中间这些函数（如 step2）的过程中，它会自动调用当前作用域内所有局部对象的析构函数。

* 如果用原生指针：内存就真的泄露了。
* 如果用 std::unique_ptr 或 std::string：它们的析构函数会被自动调用，资源被完美释放。

结论：在 C++ 中，有了异常处理，你必须全线拥抱智能指针，否则代码将变得极其不安全。

异常处理的性能开销

* 无异常发生时：现代编译器使用“无开销异常模型”（Table-based exception handling）。在不抛异常的情况下，它的性能几乎和普通代码一样。
* 有异常发生时：确实很慢。寻找 catch 块、执行栈解旋需要大量的 CPU 操作。

C++ 哲学：异常应该只用于“异常”情况。比如“硬盘坏了”、“内存耗尽”、“网络突然断了”。对于“用户输入了一个错误的密码”这种高频发生的逻辑错误，依然推荐用返回值。

**异常安全（Exception Safety）的三个等级**

* 基本保证 (Basic Guarantee)：如果异常发生，程序不会崩溃，没有内存泄露。
* 强保证 (Strong Guarantee)：“全有或全无”。如果函数失败，程序的状态会回滚到调用函数之前的状态（就像数据库事务）。
* 不抛异常保证 (No-throw Guarantee)：函数承诺绝不抛出异常。使用 noexcept 关键字标记。

**注意点**
* 不要用 throw 传递普通的业务逻辑（比如 return_user_not_found）。
* 永远不要在析构函数里抛出异常。如果析构函数抛异常，且此时正处于另一个异常触发的栈解旋过程中，程序会直接调用 std::terminate() 崩溃。