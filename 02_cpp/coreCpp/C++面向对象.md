# 1.为什么析构函数要用 virtual？
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

# 2.什么是对象切片？什么时候会发生？
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

# 3.Rule of 3 / 5 / 0 是什么？举例说明。
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

# 4.什么是RAII，RAII的本质
RAII（Resource Acquisition Is Initialization，资源获取即初始化）

## 什么是 RAII？
在 C++ 中，资源不仅指内存，还包括文件句柄、网络连接、互斥锁（Mutex）等一切“用完必须归还”的东西。RAII 的操作流程如下：
* 获取资源（构造）： 在对象的构造函数中请求资源（例如打开一个文件或申请一块内存）。
* 持有资源： 资源在对象的整个生命周期内保持有效。
* 释放资源（析构）： 在对象的析构函数中释放资源。
由于 C++ 保证在局部对象超出作用域（Scope）时会自动调用析构函数，因此无论程序是因为正常结束还是因为抛出异常退出，资源都会被百分之百安全地释放。

## RAII 的本质
如果用一句话概括 RAII 的本质，那就是：将资源的生命周期与局部对象的生命周期绑定（Binding）。
这种绑定带来了两个决定性的优势：
* 确定性（Determinism）： 与 Java 或 Python 的垃圾回收（GC）不同，RAII 确切地知道资源何时被释放——就在对象销毁的那一刻。这对于管理非内存资源（如锁和文件）至关重要。
* 异常安全（Exception Safety）： 如果函数在执行中突然抛出异常，普通的释放语句（如 fclose）可能会被跳过，但 RAII 对象的析构函数依然会被编译器自动调用。

## 常见的 RAII 例子
你可以看看在 C++ 标准库中，RAII 是如何随处可见的：

| 资源类型 | RAII 封装类 |
|---------|------------|
| 堆内存  | `std::unique_ptr`, `std::vector`, `std::string` |
| 互斥锁  | `std::lock_guard`, `std::unique_lock` |
| 文件流  | `std::fstream` |
| 线程    | `std::jthread` (C++20) |
