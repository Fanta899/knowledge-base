# Lambda 的本质
闭包与捕获

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
