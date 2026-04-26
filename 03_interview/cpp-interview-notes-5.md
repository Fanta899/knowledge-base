# C++ 后端面试笔记 — 网络/并发/分布式/存储/性能综合

---

## Reactor vs Proactor 模式

### 核心区别

| | Reactor | Proactor |
|---|---|---|
| 通知时机 | **就绪通知**：fd 可读/可写时通知 | **完成通知**：IO 已完成时通知 |
| IO 执行者 | **应用程序**自己调用 `read()`/`write()` | **内核**执行 IO，完成后回调 |
| 典型实现 | `epoll` + 事件循环 | Windows IOCP、`io_uring` |
| 编程模型 | 同步非阻塞 | 真异步 |

### epoll 上模拟 Proactor（以 Boost.Asio 为例）

```
1. 用户调用 async_read(sock, buf, on_complete)
2. 框架将操作封装为 Operation 对象，向 epoll 注册 EPOLLIN
3. epoll_wait() 返回"fd 可读"（就绪通知）
4. 框架内部调用 read(sock, buf)  ← 用户不感知
5. 读完后将 on_complete(bytes_transferred) 投递到完成队列
6. 用户回调被调用，感知上是"完成通知"
```

### io_uring — 真正的 Linux Proactor

- SQ（Submission Queue）/ CQ（Completion Queue）共享内存，内核完成 IO 后写入 CQ
- 批量提交 + 批量收割，**接近零系统调用**
- 配合 C++20 coroutine 可写出同步风格 + 真异步语义的代码

### 要点

- epoll 是**就绪通知**，本质是同步非阻塞，不是异步 IO
- Linux 旧版 AIO 只支持 `O_DIRECT` 文件，不支持 socket，基本残废
- Windows IOCP 是 Proactor 正统实现

---

## condition_variable 虚假唤醒

### 什么是虚假唤醒

`wait()` 在没有任何 `notify` 的情况下自行返回，是 POSIX 规范允许的行为（源自 Linux futex 实现）。

### 正确写法

```cpp
// 错误：if 只判断一次
if (queue.empty()) cv.wait(lk);
process(queue.front());  // 可能 crash！

// 正确：while 循环
while (queue.empty()) cv.wait(lk);

// 推荐：lambda 版本（等价于 while）
cv.wait(lk, [&]{ return !queue.empty(); });
// 内部等价于：while (!pred()) wait(lock);
```

### if vs while 的两个独立 bug 来源

| 场景 | 问题 |
|------|------|
| 虚假唤醒 | `if` 通过检查后直接执行，条件实际未满足 |
| 多消费者 `notify_all` | 2 个线程都通过 `if`，第 1 个消费了唯一任务，第 2 个对空队列操作 → **UB/crash** |

### notify_one vs notify_all

- 线程池任务入队用 `notify_one`，避免**惊群**（所有线程竞争同一把锁，只有 1 个能执行）
- C++20 `std::stop_token` 配合 `wait` 可优雅处理线程停止

---

## 客户端负载均衡 vs 服务端负载均衡

### 流量路径对比

```
服务端 LB（Nginx/LVS）：
  Client → [LB 节点] → Server A/B/C   # 多一跳

客户端 LB（gRPC/brpc）：
  Client（内置服务列表）→ 直连 Server A/B/C   # 无中间节点
```

### 对比

| | 服务端 LB | 客户端 LB |
|---|---|---|
| 额外跳数 | 有 | 无 |
| LB 节点单点风险 | 有 | 无 |
| 客户端复杂度 | 低 | 高（需集成 SDK） |
| 多语言支持 | 容易 | 每种语言各自实现 |
| 典型场景 | 南北流量（外网入口） | 东西流量（内部 RPC） |

### 选型建议

- 内部 C++ RPC：优先**客户端 LB**，减少一跳延迟
- 对外 HTTP / 多语言混合：用**服务端 LB**（Nginx）
- 多语言异构微服务：考虑 **Service Mesh（Envoy sidecar）**

### Service Mesh（xDS + Envoy）

```
业务代码 → 本机 Envoy sidecar → 对端 Envoy sidecar → 目标服务
```

- 业务代码零感知服务发现和 LB
- xDS 协议：EDS（实例列表）/ CDS（集群）/ RDS（路由）/ LDS（监听器）
- 代价：每个 Pod 多 50~100MB 内存，增加约 0.2~0.5ms 延迟

### brpc NamingService 插件

```cpp
brpc::ChannelOptions options;
options.load_balancer_name = "rr";       // 轮询
// options.load_balancer_name = "la";    // 最低延迟（实测 P99 动态倾斜）
```

支持 `list://` / `dns://` / `etcd://` / `zk://`，运行时热切换。

---

## Redis PSYNC 主从复制

### 旧版 SYNC 的问题

每次断线重连都触发**全量复制**（发整个 RDB），代价极高。

### PSYNC 的两个关键概念

| 概念 | 说明 |
|------|------|
| **Replication ID** | Master 唯一标识，重启后改变 |
| **Replication Offset** | 复制进度偏移量 |
| **Repl Backlog Buffer** | 环形缓冲区（默认 1MB），保存最近写命令 |

### 全量 vs 部分复制

```
Slave 发送：PSYNC <replid> <offset>

Master 回复：
  +CONTINUE  → 部分复制（offset 在 backlog 范围内）
  +FULLRESYNC → 全量复制（首次连接 or offset 已被覆盖 or replid 不匹配）
```

### 注意事项

- backlog 大小需根据**断线时间窗口 × 写入速率**来估算
- **Redis 6.0 PSYNC2**：故障切换后新 Master 保留旧 `replid2`，其他 Slave 仍可触发部分复制
- **无盘复制（repl-diskless-sync）**：RDB 直接通过 socket 流式传输，适合磁盘 IO 是瓶颈的场景

---

## 大页内存（Hugepage）与 TLB miss

### TLB 原理

TLB 是 MMU 中缓存虚拟地址→物理地址映射的硬件缓存。miss 后需走 4 级页表，约 **100ns+** 代价。

### Hugepage 收益

| 页大小 | 覆盖 1GB 需要的 TLB 条目 |
|--------|------------------------|
| 4KB    | 262,144 个             |
| 2MB    | 512 个                 |

典型场景 TLB miss 减少 90%+，吞吐提升 5~15%。

### 静态大页 vs 透明大页（THP）

| | 静态 Hugepage | 透明大页（THP） |
|---|---|---|
| 延迟 | 稳定 | **有抖动**（khugepaged compaction） |
| CoW 放大 | 可控 | **fork 时严重**（BGSAVE 内存翻倍） |
| 适用 | 数据库、低延迟服务 | 通用场景 |

### 生产建议

```bash
# Redis/MySQL 服务器关闭 THP
echo never > /sys/kernel/mm/transparent_hugepage/enabled
echo never > /sys/kernel/mm/transparent_hugepage/defrag

# C++ 服务使用静态大页
void* p = mmap(nullptr, size, PROT_READ|PROT_WRITE,
               MAP_PRIVATE|MAP_ANONYMOUS|MAP_HUGETLB, -1, 0);
```

- `perf stat -e dTLB-load-misses` 量化 TLB miss 次数，再决策是否引入
- Linux 5.14+ `madvise(MADV_HUGEPAGE)` 可对指定内存区域精细化启用

---

## 注册中心健康淘汰机制

### etcd Lease（租约）

```
1. Grant(TTL=10s)  → 获得 leaseID
2. Put(key, value, leaseID)  → key 与 lease 绑定
3. 每 3s KeepAlive(leaseID)  → 续期
4. 续期失败 → 10s 后 lease 过期 → key 自动删除
5. Watch 该 key 的客户端收到 DELETE 事件，移除该实例
```

### Consul TTL Check

```
注册时声明：TTL=15s，DeregisterCriticalServiceAfter=30s
服务每 5s 调用：PUT /v1/agent/check/pass/<id>

续期失败：
  15s → 状态变为 critical
  30s → 自动注销
```

### 对比

| | etcd Lease | Consul TTL Check |
|---|---|---|
| 客户端感知 | Watch **推送**，实时 | 长轮询，略有延迟 |
| 分区处理 | CP，分区时可能拒绝续期 | 可配置 AP |

### TTL 设置原则

```
续期间隔 < TTL / 3
例如：TTL=30s，续期间隔=10s（留足 2 次重试窗口）
```

### 优雅下线

不应依赖 TTL 超时（太慢），进程收到 `SIGTERM` 时**主动调用注销接口**，再等待存量请求处理完毕。

---

## 内存泄漏排查

### 确认是否泄漏

```bash
watch -n 5 'cat /proc/<pid>/status | grep VmRSS'
# VmRSS 持续单调增长且不回落 → 确认泄漏
```

### 三种工具选型

| 工具 | 阶段 | 开销 | 特点 |
|------|------|------|------|
| **AddressSanitizer** | 开发/CI | 2~3 倍内存 | 精确调用栈，同时检测越界/UAF |
| **valgrind massif** | 压测 | 10~50 倍慢 | 内存增长时间线快照 |
| **gperftools heap profiler** | 准生产 | ~5~10% | 两时间点快照 diff，可准生产用 |

### 排查流程

```
1. 监控 VmRSS 确认泄漏
2. ASan 在开发环境复现 → 能复现直接定位
3. valgrind massif 压测环境分析增长时间线
4. gperftools 准生产抓两个时间点快照做 diff
5. 定位可疑模块后 Review：
   - shared_ptr 循环引用
   - 容器只增不删（缓存无 eviction）
   - thread_local 未清理
```

### 加分点

- `jemalloc` 的 `malloc_stats_print`：零开销，快速初诊
- 容器内：`/proc/<pid>/smaps_rollup` 查看内存段详细占用

---

## perf stat 与 PMU 计数器

### 基本用法

```bash
perf stat -p <pid> sleep 10
perf stat -e cycles,instructions,LLC-load-misses -p <pid> sleep 5
```

### IPC 诊断

**IPC = Instructions / Cycles**，衡量 CPU 流水线利用率：

| IPC 范围 | 含义 |
|----------|------|
| > 2.0 | 优秀 |
| 1.0 ~ 2.0 | 正常 |
| 0.5 ~ 1.0 | 偏低，流水线停顿 |
| < 0.5 | 严重，大量 cache miss 或系统调用 |

### IPC 低的排查路径

```
IPC 低
  ├── L3 cache miss 率高？→ 数据结构缓存不友好，考虑预取/改结构
  ├── branch-misses 高？→ 分支预测失败，考虑查表代替 if-else
  └── context-switches 高？→ 锁竞争，调整线程数
```

### 完整诊断流程

```bash
# 1. 粗粒度看整体
perf stat -p <pid> sleep 10

# 2. 找热点函数
perf record -g -p <pid> sleep 30 && perf report

# 3. 生成火焰图
perf script | stackcollapse-perf.pl | flamegraph.pl > flame.svg
```

### 注意

- CPU 100% 但 IPC 0.3 → **伪忙碌**，大量时间在等内存
- `perf c2c` 专门检测 **false sharing**，直接指出哪个变量导致跨核 cache line 争用
- Intel **toplev** 工具可将停顿细分为前端/后端瓶颈，比 IPC 更精确

---

## 一致性哈希与虚节点

### 普通取模的问题

N 变化时（扩缩容），几乎所有 key 路由改变 → **大规模缓存失效 → 缓存雪崩**。

### 一致性哈希原理

```
哈希空间组织为 0 ~ 2³²-1 的环：
1. hash(节点IP) → 节点映射到环上
2. hash(key) → key 映射到环上
3. key 顺时针找到的第一个节点即为归属节点

扩缩容影响范围：平均只迁移 K/N 个 key
```

### 虚节点解决数据倾斜

每个物理节点在环上放置多个虚拟节点（通常 150~200 个）：

```cpp
std::map<uint32_t, std::string> ring;  // 有序，支持 lower_bound

// 查找归属节点
std::string getNode(const std::string& key) {
    uint32_t h = hash(key);
    auto it = ring.lower_bound(h);
    if (it == ring.end()) it = ring.begin();  // 环形绕回
    return it->second;
}
```

### 对比

| | 普通取模 | 一致性哈希+虚节点 |
|---|---|---|
| 扩缩容影响 | 几乎全部 key | 1/N 的 key |
| 负载均衡 | 均匀 | 均匀（虚节点多时） |
| 适用场景 | 固定节点数 | 动态扩缩容 |

### 加分点

- **Ketama 算法**：memcached 使用的变体，默认 160 个虚节点
- Redis Cluster 的 16384 个 slot 本质是相同思路
- 有状态服务（分布式存储）通常用 **range partition**，便于范围查询

---

## 类 Redis 内存 KV 服务设计

### 核心数据结构

#### String — SDS

```c
struct sdshdr {
    int len;    // O(1) 取长度，二进制安全
    int free;   // 预留空间，减少 realloc
    char buf[];
};
```

预分配：长度 < 1MB 时 free = len；> 1MB 时 free = 1MB。

#### Hash — ziplist / hashtable 自适应

- 元素 ≤ 128 且值 ≤ 64 字节：**ziplist**（连续内存，缓存友好）
- 超出阈值：转换为 **hashtable**（链式哈希）

#### ZSet — ziplist / skiplist + hashtable 自适应

大数据量同时维护两个结构：

| 结构 | 作用 |
|------|------|
| hashtable | `ZSCORE`：O(1) 按 member 查 score |
| skiplist | `ZRANGE`：O(logN) 按 score 范围查 |

```
跳表结构（期望 O(logN) 查找）：
level 3: 1 ──────────────→ 50
level 2: 1 ──→ 10 ───────→ 50
level 1: 1 → 5 → 10 → 30 → 50
```

### 单线程 IO 模型的优势

| 优势 | 说明 |
|------|------|
| 无锁 | 数据结构操作无需加锁 |
| 无上下文切换 | 无线程切换开销 |
| 原子性天然保证 | 命令执行期间不会被打断 |
| 缓存友好 | 数据始终在同一线程访问 |

Redis 6.0 多线程仅用于**网络读写**（序列化/反序列化），命令执行仍单线程。

### RDB vs AOF 持久化

#### RDB（快照）

```
BGSAVE 流程：
1. fork() 子进程（毫秒级）
2. 子进程序列化内存为二进制 RDB 文件
3. 父进程继续处理请求（写时复制保证一致性）
4. 完成后原子替换旧 RDB 文件
```

| 优点 | 缺点 |
|------|------|
| 文件紧凑，恢复快 | 两次快照间数据丢失 |
| fork 对父进程影响小 | CoW 可能导致内存翻倍 |

#### AOF（追加日志）

```
appendfsync always   → 每条命令刷盘，最安全最慢
appendfsync everysec → 每秒刷盘，最多丢 1s 数据（推荐）
appendfsync no       → OS 决定，性能最好，数据风险最高
```

`BGREWRITEAOF`：将内存现状重新序列化为最小命令集，压缩 AOF 文件体积。

#### 选型建议

| 场景 | 推荐 |
|------|------|
| 允许分钟级丢失，重启快 | 仅 RDB |
| 数据不能丢 | 仅 AOF（everysec） |
| 生产标准配置 | **RDB + AOF 混合**（Redis 4.0+） |

混合持久化：AOF 文件头部是 RDB 快照（恢复快），尾部是增量 AOF 命令（数据完整）。

### 加分点

- **渐进式 rehash**：扩容时每次操作迁移少量 bucket，避免单次 O(N) 阻塞
- **OBJ_ENCODING 自适应**：同一类型根据数据量自动切换底层编码
- Redis 7.0 用 **listpack** 替代 ziplist，解决连锁更新（cascade update）问题

---