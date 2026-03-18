# C++20 std::concept 核心整理
---

## 1. 什么是 Concept？
从底层看，Concept 是一个编译期谓词（Predicate）。

* 它本质上是一个返回 bool 的模板。
* 如果类型 T 满足你设定的所有条件，结果就是 true；否则就是 false。
* 零成本：它只存在于编译期，生成的机器码里找不到它。

## 2. 核心语法：requires 表达式
定义 Concept 的灵魂是 requires。它可以检查四种东西：

 **A. 简单要求（Simple Requirement）**

检查某个表达式是否能编译通过（比如方法是否存在）。
```cpp
template<typename T>
concept HasCrnti = requires(T t) {
    t.crnti(); // 只要这行代码能编译，就算过关
};
```
**B. 类型要求（Type Requirement）**

检查某个内部类型（using 或 typedef）是否存在。
```cpp
template<typename T>
concept HasContextType = requires {
    typename T::ContextInfo; 
};
```

**C. 复合要求（Compound Requirement）**

这是最强大的。不仅检查方法存在，还检查返回类型。
```cpp
template<typename T>
concept UeContext = requires(T t) {
    // 检查 t.crnti() 存在，且返回值能转换成 uint16_t
    { t.crnti() } -> std::convertible_to<uint16_t>;
};
```

**D. 嵌套要求（Nested Requirement）**

在 requires 里面再加额外的逻辑判断。
```cpp
template<typename T>
concept ValidUe = requires(T t) {
    t.ueId();
    requires sizeof(T) >= 32; // 顺便检查一下内存对齐或大小
};
```
## 3. Concept 带来的三大进化

### 1. 报错信息的“降维打击”
* 以前 (SFINAE)：报错会追溯到模板内部深处，告诉你第 500 行的某个操作失败了。
* 现在 (Concept)：报错会直接停在函数调用处，告诉你：“类型 MyType 不满足 UeContext 约束，因为它缺少 crnti() 方法”。一目了然。

### 2. 支持重载（Overload Resolution）
你可以根据 Concept 的强弱来写重载。编译器会选择“最匹配、约束最严”的那个函数。
```cpp
void send(auto& data) { /* 通用发送 */ }
void send(UeContext auto& data) { /* 针对 UE 上下文的优化发送 */ }
```

### 3. 文档即代码
以前你需要写长长的注释告诉别人这个模板参数要有什么方法。现在 UeContext auto& ctx 本身就是最完美的说明书。

### 4.示例
```cpp
namespace itf {

// 定义在 Namespace 级别
template <typename T>
concept UeCtxType = requires(const T& t) {
    { t.nrCellIdentity() } -> std::same_as<std::remove_cvref_t<itf::NrCellIdentity>>;
    { t.crnti() } -> std::same_as<std::remove_cvref_t<itf::Rnti>>;
    { t.ueIdDu() } -> std::same_as<std::remove_cvref_t<itf::UeIdDu>>;
};

class UeConnectionId {
public:
    // 使用约束。如果传入的 ueCtx 不给力，编译时瞬间抓到现行。
    explicit constexpr UeConnectionId(UeCtxType auto& ueCtx)
        : ConnectionId{ueCtx.nrCellIdentity(), ueCtx.crnti(), ueCtx.ueIdDu()}
    {}
};
}
```

### 5.总结
Concept 就像是给你的 C++ 模板加上了强类型外壳。它让泛型编程不再是“随缘编译”，而是“契约编程”。
