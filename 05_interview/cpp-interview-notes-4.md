# C++ 技术面试模拟 —— 问答汇总

---

## 题目一：Placement New 对象池

### 题目

在远程桌面图形捕获管道中，每秒需要捕获 60 帧，每帧对应一个固定大小的帧缓冲区对象。频繁的 `new`/`delete` 导致堆碎片和锁竞争，打算改用对象池。

**请设计一个基于 `placement new` 的固定大小对象池，并说明：在什么情况下必须手动调用析构函数，为什么？**

### 问答过程

**Q：何时必须手动调用析构函数？**  
A（候选人）：使用普通 `new` 申请一块大内存，在此之上用 `placement new` 分配对象，只构造不涉及堆内存分配，这种对象必须手动析构，因为不涉及内存释放。

**追问：对象内部有 `std::vector<uint8_t>` 成员时，不手动析构会怎样？**  
A（候选人）：会造成内存泄漏。

**追问：`delete[] pool` 会不会顺带析构池上的对象？对齐是否足够？**  
A（候选人）：FrameBuffer 无法析构或这块内存永久泄漏。（部分正确，需补充）

### 参考答案

**核心要点**

1. `placement new` 只构造对象，不分配内存；销毁时必须显式调用 `p->~T()`，由池归还槽位，最后统一 `delete[]` 释放大块内存
2. 必须手动析构的根本原因：**对象生命周期与内存生命周期已解耦**，编译器/运行时不知道这块内存上住着什么类型，无法自动调用正确的析构函数
3. `delete[] pool` **会**释放那块大内存，但不会调用 `FrameBuffer` 的析构函数，导致 `vector` 等成员持有的堆资源泄漏
4. 对齐陷阱：`new char[N]` 只保证 `alignof(char)=1`，放置高对齐类型是 UB

```cpp
// 正确做法：保证对齐
alignas(FrameBuffer) std::byte pool[sizeof(FrameBuffer) * POOL_SIZE];

// 或动态分配时指定对齐
void* pool = ::operator new(sizeof(FrameBuffer) * N,
                             std::align_val_t{alignof(FrameBuffer)});

// 构造
FrameBuffer* p = new (slot) FrameBuffer();

// 回收：先析构，再归还槽位
p->~FrameBuffer();
```

**常见误区**
- 认为 `delete[] pool` 会顺带析构池上的对象——不会
- 用 `char[]` 或 `malloc` 直接当池，在 SIMD 对齐要求严格场景下崩溃或性能劣化

**加分点**
- 用 `std::array<std::byte, N>` + `alignas` 管理内存
- 结合 `std::pmr::monotonic_buffer_resource` 可免去手写 freelist
- 析构顺序应与构造顺序相反（LIFO）

---

## 题目二：shared_mutex 与写者饥饿

### 题目

图形捕获管道中，帧元数据（分辨率、色彩空间、时间戳）存储在共享结构体中。捕获线程偶尔更新，编码线程和网络发送线程每帧都读取——读写比约 **1000:1**。

**`std::shared_mutex` 在此场景下合适吗？实现层面如何区分读写锁？写操作被读者"饿死"如何解决？**

### 问答过程

**Q：shared_mutex 合适吗？**  
A（候选人）：合适，写操作非常少，写锁用 `lock()`，读锁用 `lock_shared()`。有写者等待时阻塞读者优先调度写者。

**追问：写者优先是标准保证还是实现相关？Linux/Windows 行为是否一致？**  
A（候选人）：Linux 默认倾向于读者优先，Windows 尽量避免写者饥饿。

**追问：有没有完全绕开读写锁、让读路径零竞争的方案？**  
A（候选人）：采用 event 通知或 callback 形式，写者写入时触发所有读者读取一次。（思路正确但未回答零竞争）

### 参考答案

**核心要点**

1. `std::shared_mutex` 基本合适，但跨平台饥饿行为不可依赖，标准层面仅保证"不能同时持有读锁和写锁"，对饥饿策略无规定

2. **SeqLock（序列锁）**——读路径真正零竞争：

```cpp
struct SeqLock {
    std::atomic<uint32_t> seq{0};
    FrameMeta data;

    void write(const FrameMeta& m) {
        seq.fetch_add(1, std::memory_order_release); // 奇数：写进行中
        data = m;
        seq.fetch_add(1, std::memory_order_release); // 偶数：写完成
    }

    FrameMeta read() const {
        FrameMeta out;
        uint32_t s1, s2;
        do {
            s1 = seq.load(std::memory_order_acquire);
            if (s1 & 1) continue;   // 写进行中，自旋
            out = data;
            s2 = seq.load(std::memory_order_acquire);
        } while (s1 != s2);         // 读期间发生写，重试
        return out;
    }
};
```

3. **RCU 风格**：写者 copy-on-write 新副本，`atomic` 指针原子替换，读者用 `shared_ptr` 保活旧副本

**常见误区**
- 认为 `std::shared_mutex` 的饥饿行为是标准保证的
- SeqLock 中忘记 `data` 的读写也需要正确内存序，否则编译器可能重排

**加分点**
- SeqLock 要求 `data` 是 trivially copyable，否则读到中间状态是 UB
- Linux 内核的 `seqlock_t` 正是此思路，用于保护时钟等高频只读数据

---

## 题目三：Windows minifilter 驱动与文件 I/O 拦截

### 题目

Workspace App 的"配置文件重定向"功能需要透明拦截 `C:\Users\username\AppData` 下的文件读写，将其重定向到网络共享或本地缓存，应用程序本身无感知。

**Windows minifilter 驱动的工作原理是什么？处于 I/O 栈哪层？用户态有哪些替代方案？**

### 问答过程

**Q：minifilter 工作原理？**  
A（候选人）：操作前回调——IO 请求到来时先处理，处理完要么继续向下传递，要么直接结束请求。操作后回调——下层驱动处理完后返回给用户之前进入。处于用户和内核之间。

**追问：minifilter 实际挂载在内核 I/O 栈哪层？多个 minifilter 如何决定执行顺序？**  
A（候选人）：文件系统之上，通过设置海拔（Altitude）来决定执行顺序。

**用户态替代方案？（候选人表示不清楚，直接给出答案）**

### 参考答案

**核心要点**

1. minifilter 挂载在**文件系统驱动（NTFS）之上、I/O Manager 之下**，完全在内核态，不在用户/内核边界
2. Altitude 值越大越靠近应用层先执行，Microsoft 对不同功能类别划定了海拔范围（备份/加密/反病毒）

3. **用户态替代方案**：

| 方案 | 原理 | 适用场景 |
|------|------|----------|
| **IAT Hook** | 修改目标进程导入地址表，替换 `CreateFile`/`ReadFile` 函数指针 | 拦截单个进程 |
| **Detours / inline hook** | 在函数入口写跳转指令，重定向到自己的实现 | 微软 Detours 库，企业级使用 |
| **DLL 注入 + hook** | 将 DLL 注入目标进程后再做 hook | 需要 `CreateRemoteThread` 或 `AppInit_DLLs` |
| **ProjFS（Windows Projected File System）** | 用户态实现虚拟目录，系统级透明重定向，无需注入 | Win10 1809+，最干净的方案 |

**常见误区**
- 认为用户态 hook 对所有进程都生效——DLL 注入只影响被注入的进程
- IAT Hook 只能拦截通过 IAT 调用的函数，`GetProcAddress` 动态调用的会绕过

**加分点**
- Detours patch 函数入口时需处理多线程竞争，需配合 `DetourUpdateThread` 暂停所有线程
- minifilter 需要 EV 代码签名证书（Windows 10 后强制），用户态方案绕开了此部署门槛
- Git for Windows（VFS for Git）使用 ProjFS 实现虚拟文件系统

---

## 题目四：抖动缓冲区（Jitter Buffer）

### 题目

音频重定向通过 UDP 传输，网络抖动导致相邻包到达间隔从 10ms 波动到 80ms，而音频播放设备要求稳定连续地喂入数据，否则破音卡顿。

**抖动缓冲区的核心设计原理？固定与自适应缓冲区各有什么问题？自适应缓冲区应根据哪些指标动态调节？**

### 问答过程

**Q：抖动缓冲区设计原理？**  
A（候选人）：用延迟换稳定。自适应缓冲区可以动态调整 buffer size，基于平均 delay 和抖动。

**追问：缩小缓冲时如何在不破坏播放连续性的前提下"压缩"缓冲队列？**  
A（候选人）：通过时间尺度调整、静音压缩和渐进对齐，在用户感知不到的范围内"偷时间"。

### 参考答案

**核心要点**

1. **基本原理**：接收端将乱序、不均匀到达的包按序号重排，在播放时钟驱动下匀速取出，以缓冲深度吸收抖动——**以延迟换平滑**

2. **固定缓冲的问题**：
   - 太小：抖动峰值超过缓冲深度时丢包，破音
   - 太大：引入固定延迟，实时通话体验差

3. **自适应调节指标**：
   - 包间隔抖动的**滑动方差**（RFC 3550 定义的 jitter 估算）
   - 近期**丢包率**
   - 网络 RTT 趋势

4. **缩小缓冲的无损方案**：

| 技术 | 原理 |
|------|------|
| **WSOLA/TSM 时间尺度调整** | 时域轻微压缩音频（<10% 速率变化人耳无感知） |
| **静音压缩** | 检测到静音段时直接丢帧，成本最低 |
| **渐进对齐** | 每次播放少取一点，分摊到数百帧内完成收敛 |

**常见误区**
- 用平均延迟而非**抖动方差**作为调节信号
- 扩大缓冲反应太慢，应在抖动上升趋势时提前扩大

**加分点**
- WebRTC NetEQ 用 **PLC（Packet Loss Concealment）** 填补真正丢失的包，对语音用波形外推
- 调节步长不对称：扩大应**激进快速**，缩小应**保守缓慢**

---

## 题目五：SIMD 自动向量化失败与手动优化

### 题目

图形捕获管道中，BGRA→YUV420 格式转换函数每帧约 800 万像素，需在 16ms 内完成。反汇编发现编译器未对此循环进行 SIMD 向量化，全为标量指令，性能是参考实现的 4 倍差距。

**编译器自动向量化失败的常见原因？如何排查修复？手写 SIMD intrinsics 有哪些陷阱？**

### 问答过程

**Q：向量化失败原因？**  
A（候选人）：数据没有对齐、有数据依赖、内存不连续。手动写 SIMD 需要注意跨平台不可用。

**追问：`src` 和 `dst` 两个指针参数，编译器为何可能拒绝向量化？如何告诉编译器"指针不重叠"？**  
A（候选人）：使用 `alignas`？（不正确）

### 参考答案

**核心要点**

1. **自动向量化失败的三大原因**：

| 原因 | 说明 | 修复方式 |
|------|------|----------|
| **指针别名（aliasing）** | 编译器不确定 `src` 和 `dst` 是否重叠，不敢重排读写 | `__restrict__`（GCC/Clang）/ `__restrict`（MSVC） |
| **数据依赖** | 后一次迭代依赖前一次结果（累加、滤波） | 重写算法消除依赖，或手写 SIMD |
| **未对齐访问** | 数据地址不满足 16/32 字节对齐 | `alignas(32)` + `__builtin_assume_aligned` |

2. **别名问题修复**（`alignas` 解决对齐，**不能**解决别名）：

```cpp
void convert(const uint8_t* __restrict__ src,
             uint8_t* __restrict__ dst, int N) {
    for (int i = 0; i < N; i++)
        dst[i] = src[i] * 2;  // 编译器现在敢向量化了
}
```

3. **手写 SIMD intrinsics 的陷阱**：
   - **跨平台不可用**：`_mm256_*` 是 AVX2，ARM 需要 NEON，要用 `#ifdef` 或抽象层（highway、xsimd）
   - **未对齐加载崩溃**：`_mm256_load_si256` 要求 32 字节对齐，未对齐用 `_mm256_loadu_si256`
   - **尾部处理**：N 不是向量宽度整数倍时需要标量处理剩余元素

**常见误区**
- 用 `alignas` 期望解决别名问题——`alignas` 只管对齐，不影响别名分析
- 不看编译器向量化报告就凭感觉猜原因（应用 `-fopt-info-vec` 或 `-Rpass=loop-vectorize`）

**加分点**
- `[[clang::vectorize(enable)]]` 可强制尝试向量化并输出失败原因
- BGRA→YUV 这类固定模式直接用 `libyuv` 库，内部已针对各平台 SIMD 优化

---
