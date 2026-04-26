# 1. 查找map，key不存在即构造插入，存在即修改更新value

**结论（直接用这个）**

* key 不存在 → 构造 V
* key 存在 → 更新 V

**推荐写法（性能最优、语义清晰）**
```cpp
auto [it, inserted] = m.try_emplace(key, ctor_args...);
if (!inserted) {
    it->second.update(update_args...);
}
```
* ✔ 一次红黑树查找
* ✔ 只有不存在时才构造 V
* ✔ 存在时不发生任何多余构造 / 赋值

**为什么不是 insert_or_assign？**
```cpp
m.insert_or_assign(key, new_value);
```
* ❌ 总是要构造一个完整的 V
* ❌ 无法区分 “构造” vs “更新逻辑”
* ❌ 更新成本可能远大于 update()

如果你的 V：
* 构造很重（内存 / IO / 复杂初始化）
* 更新只是改几个字段

👉 insert_or_assign 会浪费性能

**和“先 find 再分支”的对比**

❌ 次优写法

```cpp
auto it = m.find(key);
if (it == m.end()) {
    it = m.emplace(key, V{ctor_args...}).first;
} else {
    it->second.update(update_args...);
}
```
问题：
* ❌ 2 次树查找
* ❌ 代码冗长
* ❌ cache / branch predict 更差

**最优写法的本质优势**

try_emplace 干了三件事：

* 一次查找
* 必要时原地构造 V
* 返回是否插入成功

**性能对比总结表**

| 写法                     | 树查找   | 构造次数      | 更新代价 | 推荐度   |
| ---------------------- | ----- | --------- | ---- | ----- |
| `try_emplace + update` | **1** | **0 / 1** | 最小   | ⭐⭐⭐⭐⭐ |
| `insert_or_assign`     | 1     | **1**     | 覆盖   | ⭐⭐⭐   |
| `operator[]`           | 1     | **1（默认）** | 更新   | ⭐⭐    |
| `find + emplace`       | 2     | 0 / 1     | 更新   | ⭐     |


**构造和更新不是同一件事时：**

👉 try_emplace + if (!inserted) update 永远是最优解

# 2. 查找unordered_map，key不存在即构造插入，存在即修改更新value

try_emplace 在 unordered_map 下仍然最优，而且优势更明显

原因：
* 只做一次 hash + bucket 查找
* 只有 key 不存在才构造 V
* 避免临时 value 构造

**unordered_map 下“性能最佳”的完整写法**

强烈推荐：提前 reserve
```cpp
m.reserve(expected_size);
```
插入 / 更新逻辑
```cpp
auto [it, inserted] = m.try_emplace(key, ctor_args...);
if (!inserted) {
    it->second.update(update_args...);
}
```
为什么 reserve 很重要？
* 减少 rehash 次数
* 稳定延迟
* 避免 iterator 失效

**find + emplace 在 unordered_map 下更糟**

成本：
* ❌ 2 次 hash
* ❌ 2 次 bucket 查找
* ❌ 可能 2 次触发 rehash 判断

**性能对比速览表（unordered_map）**
| 写法                     | hash 次数 | 构造 V      | rehash 风险 | 推荐度   |
| ---------------------- | ------- | --------- | --------- | ----- |
| `try_emplace + update` | **1**   | 0 / 1     | 最低        | ⭐⭐⭐⭐⭐ |
| `insert_or_assign`     | 1       | **1**     | 中         | ⭐⭐⭐   |
| `operator[]`           | 1       | **1（默认）** | **高**     | ⭐⭐    |
| `find + emplace`       | **2**   | 0 / 1     | 中         | ⭐     |

**unordered_map + try_emplace + reserve 才是可控 latency 的组合**

# 3.推荐用法总结
✅ 查key
```cpp
auto it = m.find(key);
```
✅ 查 + 插（不覆盖）
```cpp
auto [it, inserted] = m.try_emplace(key, args...);
```
✅ 查 + 插 / 覆盖
```cpp
auto [it, inserted] = m.insert_or_assign(key, value);
```
❌ 不推荐
```cpp
if (m.find(key) == m.end()) {
    m.emplace(key, value);
}
```
## 哪些情况下 find_if 反而是对的？
✅ 条件不是 key
```cpp
// 按 value 查
std::ranges::find_if(m, [](auto& kv) {
    return kv.second > 100;
});
```
✅ 多条件组合
```cpp
std::ranges::find_if(m, [](auto& kv) {
    return kv.first > 10 && kv.second.is_valid();
});
```
✅ 视图组合（可读性优先）
```cpp
auto it = std::ranges::find_if(
    m | std::views::filter(pred1),
    pred2
);
```
## 一句话工程结论（给面试官 / code review 用）
try_emplace 是“查找 + 条件构造 + 插入”的最优融合接口在 std::map / std::unordered_map 中，几乎总是优于手写组合
