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

---

# 5. CRTP
**CRTP 是为了实现“没有虚函数开销的多态”**, 在编译期就固定下来的“函数指针表”，而不是在运行期去查表。

## 5.1. CRTP 的基本形态
```cpp
// 1. 定义模板父类
template <typename Derived>
class Base {
public:
    void interface() {
        // 关键：将 this 指针强转为模板参数指定的子类指针
        // 然后调用子类的实现
        static_cast<Derived*>(this)->implementation();
    }
};

// 2. 子类继承时，把自己传给父类
class Derived : public Base<Derived> {
public:
    void implementation() {
        std::cout << "Derived implementation called!" << std::endl;
    }
};
```

## 5.2 对比虚函数
虚函数（动态多态）的代价：
* 内存开销：每个对象多一个 vptr 指针，每个类多一张 vtable 表。
* 运行开销：调用时需要通过 vptr 查 vtable，再跳转地址，无法进行内联优化（Inline）。
* 适用场景：你直到运行那一刻才知道对象是谁（比如一个 vector<Animal*> 里面既有猫又有狗）。

CRTP（静态多态）的优势：
* 零内存开销：对象大小就是数据成员的大小，没有多余指针。
* 极致性能：因为代码在编译期就确定了，编译器可以把子类的代码直接内联到调用处。
* 适用场景：你在写代码时就知道具体的类型（比如特定的驱动程序、特定的算法实现）。


## 5.3 CRTP 的三大实战用途
* 静态接口约束 (Static Interface)
    * 这类似于 Java 的接口或 C++ 的纯虚函数，但它发生在编译期。如果你忘记在子类实现 implementation()，编译器会直接报错，而不是等到运行时崩掉。

* 扩展功能（Mixin / 插槽）
    * 这是 CRTP 最强大的地方：父类可以为子类“自动注入”功能。比如你想让很多类都支持“克隆（Clone）”功能：
```cpp
template <typename Derived>
class Cloneable {
public:
    Derived* clone() const {
        return new Derived(static_cast<const Derived&>(*this));
    }
};

// 只要继承一下，MyClass 就立刻拥有了精准返回 MyClass* 的 clone 方法
class MyClass : public Cloneable<MyClass> {
    // ...
};
```
* 计数器（Object Counter）
    * 如果你想统计某个类当前有多少个活跃对象（C 语言里你可能得手动在 init 和 destroy 里改全局变量）：
```cpp
template <typename T>
class Counter {
    static inline int count = 0; // C++17 静态成员初始化
protected:
    Counter() { count++; }
    ~Counter() { count--; }
public:
    static int get_count() { return count; }
};

class User : public Counter<User> {};
class Task : public Counter<Task> {}; // User 和 Task 的计数器是完全独立的
```


你可能会问：Base<Derived> 在解析的时候，Derived 还没定义完呢，这合法吗？

**真相是**：在 C++ 中，当子类 Derived : public Base<Derived> 声明时，Derived 被视为一个**“不完整类型（Incomplete Type）”。而模板函数的成员函数（如 interface()）只有在被调用**时才会实例化。到那个时候，Derived 已经定义完成了。

这就是为什么 CRTP 能够“反向调用”子类的函数。

| 特性	    |  虚函数 (Dynamic)	 | CRTP (Static) |
|---------  |------------| ---------- |
| 决策时间	 |  运行期 (Runtime) | 编译期 (Compile-time) |
| 性能	    |  有查表开销，难内联	 |  零开销，易内联   |
| 灵活性	| 高（支持异质容器存储）	| 低（每个子类的父类类型都不同）  |
| 代码体积	| 较小	     | 较大（每个特化都会生成一份代码） |

**什么时候用？** 如果你在写底层驱动、数学库、或者性能敏感的中间件，且不需要在运行时动态切换对象类型，CRTP 是你超越 C 语言性能上限的神器。

# 6. vtable和vptr
# 6.1. 数量对比：1 对 N

这是最基础也最重要的区别：

* vtable (Virtual Table)：每个类（Class）一份。
    * 只要你的类里有 virtual 函数，编译器就会为这个类生成一张虚函数表。它是静态的，存储在 .rodata（只读数据段）。
* vptr (Virtual Pointer)：每个对象（Instance）一份。
    * 每当你 new 一个对象或者在栈上创建一个对象，这个对象的内存空间里就会多出一个指针，指向它所属类的那张 vtable。

# 6.2. 生命周期：编译期 vs 构造期

它们被创建和关联的时间点完全不同：

* vtable：编译期确定。
    * 编译器在编译代码时，扫描类的声明，发现有虚函数，就计算好函数地址，填入 vtable。
* vptr：构造期初始化。
    * 当你调用构造函数时，编译器悄悄插入了一行代码。这行代码的作用是：将该对象的 vptr 指向对应类的 vtable 起始地址。

面试陷阱：为什么构造函数不能是虚函数？

答案：因为调用构造函数时，vptr 还没初始化好。如果没有 vptr 指向 vtable，程序根本找不到虚函数的入口。

# 6.3. 调用过程：三次“跳跃”

当你写 ptr->virtual_func() 时，CPU 实际上完成了以下三步关系映射：

* 找到 vptr：程序根据对象的起始地址，读取前 8 个字节（即 vptr）。
* 定位 vtable：根据 vptr 存储的地址，跳转到对应类的 vtable 所在的内存区域。
* 获取函数地址：根据函数在表中的偏移量（Offset），拿到真实的函数代码地址，最后执行跳转。

# 6.4. 继承关系中的演变

在继承场景下，它们的关系会变得更有趣：

* 单继承：
    * 子类拥有一张完全属于自己的 vtable。
    * 如果子类重写了父类的函数，子类 vtable 里的地址会换成子类函数的地址；如果没有重写，则拷贝父类函数的地址。

* 多重继承：
    * 子类对象会有多个 vptr，分别指向对应父类接口的 vtable。这也就是为什么多重继承会让对象变大的原因。

# 6.5. 总结表
| 特性 | vtable (虚函数表) | vptr (虚函数指针) |
| -------- | ------- | --------- |
| 所属关系 | 属于 类 (Class) | 属于 对象 (Instance) |
| 存储位置 | 只读数据段 (.rodata) | 对象内存空间 (通常在开头) |
| 创建时间 | 编译期 (Compile-time) | 构造期 (Construction) |
| 内存占用 | 全局只有一份数组 | 每个对象增加一个指针大小 (8字节) |
| 本质 | 函数指针数组 | 指向该数组的指针 |
