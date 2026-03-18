# C++20 std::ranges 核心整理
---

## 1. std::ranges 是什么（What）

`std::ranges` 是 C++20 引入的一套 **基于“区间（range）”的算法与视图（view）体系**，
用于：

* 提高算法可读性
* 降低错误率（迭代器不匹配）
* 支持 **惰性（延迟）计算**

一句话：

> **ranges = 算法 + 约束 + 视图（views）**

---

## 2. 为什么要有 ranges（Why）

### 2.1 传统 STL 的问题

```cpp
std::vector<int> v{1,2,3,4,5};
std::copy_if(v.begin(), v.end(), out.begin(), pred);
```

问题：

* begin / end 容易写错
* 算法组合困难
* 中间结果必须落地成容器

---

### 2.2 ranges 解决了什么

```cpp
std::ranges::copy_if(v, out, pred);
```

或者：

```cpp
auto r = v | std::views::filter(pred) | std::views::take(3);
```

优势：

* 不暴露迭代器
* 支持管道式组合
* 不产生中间容器（view）

---

## 3. 核心概念总览

| 概念        | 说明                              |
| --------- | ------------------------------- |
| range     | 可遍历的对象（有 begin/end）             |
| view      | **不拥有数据**的 range（惰性）            |
| algorithm | 接受 range 的算法                    |
| adaptor   | 生成 view 的工具（filter / transform） |

---

## 4. view 为什么是“延迟执行”（重点）

### 4.1 结论

> **view 本身不执行任何逻辑**，
> **只有在遍历（iteration）发生时才执行**。

---

### 4.2 示例说明

```cpp
auto r = v | std::views::filter([](int x) {
    std::cout << "filter " << x << '\n';
    return x % 2 == 0;
});
```

此时：

* ❌ 不会打印任何东西
* 只是构造了一个 view 对象

---

### 4.3 真正执行的时机

```cpp
for (int x : r) {
    std::cout << x << '\n';
}
```

输出时：

* 每次 `++iterator`
* 触发 filter 判断

📌 **执行发生在算法 / for-range 中**

---

## 5. view ≠ container（非常重要）

| 对比     | view | container |
| ------ | ---- | --------- |
| 是否拥有数据 | ❌    | ✅         |
| 是否拷贝元素 | ❌    | ✅         |
| 生命周期   | 依赖源  | 自持        |
| 是否惰性   | ✅    | ❌         |

---

## 6. 生命周期陷阱（高频坑）

### 6.1 错误示例

```cpp
auto make_view() {
    std::vector<int> v{1,2,3,4};
    return v | std::views::filter([](int x){ return x > 2; });
}
```

问题：

* 返回的 view 引用已销毁的 `v`
* **悬空引用（UB）**

---

### 6.2 正确方式

```cpp
auto v = std::vector<int>{1,2,3,4};
auto r = v | std::views::filter(pred);
```

或：

```cpp
auto r = std::views::iota(1, 10)
       | std::views::filter(pred);
```

---

## 7. view 的常见组合模式

### 7.1 filter + transform

```cpp
auto r = v
  | std::views::filter([](int x){ return x % 2 == 0; })
  | std::views::transform([](int x){ return x * x; });
```

---

### 7.2 take / drop / reverse

```cpp
auto r = v
  | std::views::drop(2)
  | std::views::take(3)
  | std::views::reverse;
```

---

## 8. ranges 算法 vs 传统算法

### 8.1 ranges::for_each

```cpp
std::ranges::for_each(v, [](int x){
    std::cout << x << '\n';
});
```

等价于：

```cpp
std::for_each(v.begin(), v.end(), ...);
```

但：

* 不需要 begin/end
* 约束更严格

---

## 9. ranges::copy 能操作什么？

```cpp
std::ranges::copy(r, out);
```

要求：

* `r` 是 input_range
* `out` 满足 output_iterator

📌 **view 完全可以作为输入**

---

## 10. 什么时候不用 view（工程经验）

不适合的情况：

* 多次遍历同一结果
* 需要随机访问
* 需要缓存中间结果

👉 此时应：

```cpp
std::vector<int> result(r.begin(), r.end());
```

---

## 11. 面试高频总结

* view 是不是立即执行？ → ❌
* view 是否拥有数据？ → ❌
* 执行发生在哪里？ → **算法 / 遍历时**
* 最大的坑？ → **生命周期**

---

## 12. 一句话总结

> **std::ranges = 用惰性 view 组合数据流，用算法触发执行**

---

## Related

* [[cpp/memory_model]]
* [[为什么_view_是延迟执行]]
