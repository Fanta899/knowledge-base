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

与 C 语言代码兼容, c_str()返回原始字符指针
```cpp
std::string cppStr = "Hello World";
// 如果一个 C 函数要求 const char* 参数
printf("%s\n", cppStr.c_str());
```
注意： 不要保存 .c_str() 返回的指针，因为如果 cppStr 被修改或销毁，那个指针就变成了悬空指针。

⭐ 面试总结金句

* std::string 是连续内存、RAII 管理的值类型；
* 拷贝是深拷贝，移动是资源转移；
* 性能敏感的只读场景优先使用 std::string_view，
* 注意内存重分配会使指针和引用失效。
