# C++ 后端面试笔记 — 第六期

> 覆盖：epoll ET/LT、io_uring、CAP/Consul、API 网关、HTTP 演进、读写锁、LRU、fd 耗尽、gRPC 流模式、同步驱动阻塞

---

## A1. epoll ET vs LT

### 核心区别

| | LT（水平触发） | ET（边缘触发） |
|---|---|---|
| 触发时机 | buffer 有数据就持续上报 | 仅在状态变化（新数据到来）时触发一次 |
| 遗漏数据风险 | 无 | 有，未读完不再通知 |
| fd 属性要求 | 阻塞/非阻塞均可 | **必须 `O_NONBLOCK`** |
| 系统调用次数 | 较多 | 更少 |

### ET 必须循环读到 EAGAIN 的原因

ET 只在 buffer 从空变非空时通知一次。若未读完，内核不再触发，剩余数据永远滞留，连接"卡死"。

### ET 必须配合 O_NONBLOCK 的原因

循环 read 到 buffer 排空时，阻塞 fd 会挂起整个事件循环线程；非阻塞 fd 立即返回 `EAGAIN`，循环终止，线程继续处理其他事件。

### ET 正确读取模式

```cpp
void handle_read(int fd) {
    char buf[4096];
    while (true) {
        ssize_t n = read(fd, buf, sizeof(buf));
        if (n > 0) {
            process(buf, n);
        } else if (n == 0) {
            close(fd);
            break;
        } else {
            if (errno == EAGAIN || errno == EWOULDBLOCK) break;  // buffer 已空
            else if (errno == EINTR) continue;                   // 信号中断，重试
            else { close(fd); break; }
        }
    }
}
```

### 多线程高并发下的标准范式：ET + EPOLLONESHOT

```cpp
// 注册
ev.events = EPOLLIN | EPOLLET | EPOLLONESHOT;
epoll_ctl(epfd, EPOLL_CTL_ADD, fd, &ev);

// 处理完毕后重新投递
ev.events = EPOLLIN | EPOLLET | EPOLLONESHOT;
epoll_ctl(epfd, EPOLL_CTL_MOD, fd, &ev);
```

`EPOLLONESHOT` 保证同一时刻同一 fd 只有一个线程处理，避免数据竞争。

### 工程选型

| 场景 | 推荐 |
|------|------|
| 单线程 Reactor（如 Redis） | LT |
| 多线程高吞吐（如 Nginx、brpc） | ET + EPOLLONESHOT + 非阻塞 fd |
| 对编程复杂度敏感的业务服务 | LT |

---

## A2. io_uring vs epoll

### 本质差异

| | epoll | io_uring |
|---|---|---|
| 模型 | 就绪通知（Reactor） | 提交-完成（接近 Proactor） |
| IO 执行者 | 用户自己调 read/write | 内核执行，结果写入 CQ |
| 队列 | 无共享队列 | SQ / CQ 共享内存环形队列 |
| syscall 次数 | 每次 IO 都需要 syscall | 批量提交，可配合 SQPOLL 趋近于零 |

### SQ/CQ 职责

- **SQ（Submission Queue）**：用户写入 SQE，描述"要做什么 IO"
- **CQ（Completion Queue）**：内核写入 CQE，返回"操作完成结果"
- 口诀：**user produces SQE，kernel consumes SQE；kernel produces CQE，user consumes CQE**

### io_uring 不是所有场景的纯 Proactor

- 部分操作可以真正异步完成
- 部分操作退化为内核 worker 线程池代执行（如 buffered 文件 IO）
- io_uring 更准确的定位：**统一异步提交与完成框架**

### 关键高性能特性（Asio 封装下用不到）

| 特性 | 作用 |
|------|------|
| `SQPOLL` 模式 | 内核轮询线程主动消费 SQ，用户侧 syscall 趋近零 |
| Fixed buffer | 注册固定 buffer，避免每次 IO pin/unpin 用户内存 |
| Linked requests | 把 recv→process→send 串成链，单次提交批量完成 |
| 批量 CQE 消费 | `io_uring_for_each_cqe` 一次处理多个完成事件 |

### Boost.Asio 的局限

- 双后端（epoll / io_uring）是**编译期决定**（`BOOST_ASIO_HAS_IO_URING` 宏），无法运行时切换，回滚需重新编译部署
- Asio 是薄封装，SQPOLL、fixed buffer、linked requests 等高性能特性无法使用
- 适合"跨平台、不追求极致性能"场景；追求极致 P99 应用裸 `liburing`

### 选型建议

| 场景 | 选择 |
|------|------|
| 稳定性优先、团队熟悉 epoll | epoll |
| 极高吞吐、syscall 是瓶颈、内核版本可控 | io_uring + liburing |
| 跨平台或降低开发成本 | Boost.Asio |

### 渐进式迁移方案（工程实践）

1. 先用 `epoll` + 批量写（`writev`）压榨现有方案极限，确认瓶颈真实存在
2. 引入 `liburing`，在独立模块验证 SQPOLL + fixed buffer 收益
3. 新旧路径通过**运行时 feature flag**（非编译宏）控制，保留热切换能力
4. 重点监控指标：P99/P999 延迟、syscall 次数（`perf stat`）、CPU 上下文切换次数
5. 以下情况直接否决上线：内核版本 < 5.10、驱动不支持原生 async、线上排障工具不成熟

---

## K1. CAP 定理与 Consul

### Consul 的选择：CP

网络分区时，少数派节点（基于 Raft）无法确认数据最新，**拒绝读写而非返回可能过期数据**。

### 为什么服务发现选 CP 正确

- 返回过期服务地址 → 流量打到已下线节点 → 请求失败
- 宁可让调用方感知"发现不可用"，也不能拿脏地址去建连

### CP vs AP 对比

| 选型 | 代表 | 分区时行为 | 适用场景 |
|------|------|-----------|---------|
| CP | Consul、etcd、ZooKeeper | 拒绝读写 | 服务发现、分布式锁、配置中心 |
| AP | Eureka、Cassandra | 返回可能过期数据 | 购物车、Feed 流、允许短暂不一致 |

### 常见误区

- CAP 的 P（分区容忍）在分布式系统中几乎无法放弃，实际只在 C 和 A 之间选择
- CAP 是极端情况下的权衡，正常情况 Consul 既一致又可用
- 很多系统通过**可调一致性级别**（如 Cassandra `QUORUM`）在两者间动态权衡

---

## L4. API 网关设计

### 请求处理链路（拦截器链）

```
Client → 接入层（epoll 事件循环）
       → 协议解析（HTTP/1.1 or HTTP/2）
       → 鉴权（JWT RS256 / API Key）      ← 失败返回 401
       → 限流（令牌桶，单机+Redis双层）    ← 超限返回 429
       → 路由（Trie 前缀树匹配）
       → 熔断检测（三态状态机）            ← Open 状态返回降级响应
       → 后端转发（连接池 + deadline 传播）
       → 响应回写 + 异步日志采集
```

### 各模块 C++ 实现要点

| 模块 | 实现要点 |
|------|---------|
| 鉴权 | OpenSSL `EVP_DigestVerify` 验签；API Key 用 `unordered_map` + 读写锁缓存 |
| 限流 | 令牌桶用 `std::atomic` + `std::chrono` 无锁；跨实例用 Redis Lua 脚本保证原子性 |
| 路由 | Trie 前缀树支持通配符；路由表热更新用 `shared_ptr` + RCU 双 buffer |
| 熔断 | 滑动窗口统计错误率，`std::atomic` 维护三态状态机；Half-Open 用定时器触发探测 |
| 日志 | 主路径写 SPSC ring buffer，后台线程批量刷盘，包含 TraceID |
| 连接池 | 每后端维护连接池，空闲超时检测；RAII guard 防连接泄漏 |

### 常见误区

- 鉴权放在限流之后（应先鉴权再限流，否则非法请求也消耗令牌）
- 日志同步写阻塞主路径
- 熔断状态用普通 int 而非 `std::atomic`，多线程下状态撕裂

---

## M7. HTTP/1.1 → HTTP/2 → HTTP/3 演进

### HOL Blocking 的层次

| | 应用层 HOL | 传输层 HOL | 底层协议 |
|--|-----------|-----------|---------|
| HTTP/1.1 | 有（串行请求） | 有 | TCP |
| HTTP/2 | **无**（多路复用 Stream） | **有**（TCP 丢包阻塞所有 Stream） | TCP |
| HTTP/3 | 无 | **无**（QUIC 独立 Stream） | UDP + QUIC |

### HTTP/2 传输层 HOL 的根本原因

HTTP/2 跑在 TCP 上。TCP 保证有序交付——任意一个报文段丢失，后续所有报文段（属于不同 Stream）都必须等待重传，全部 Stream 阻塞。

### HTTP/3 的根本解法

- 传输层换成 UDP + QUIC，QUIC 在应用层自己实现可靠传输
- 每个 QUIC Stream 独立管理丢包重传，Stream A 丢包不影响 Stream B/C
- 额外收益：**连接迁移**（Connection ID，切换网络无需重新握手）、0-RTT/1-RTT 握手

---

## N10. 读写锁实现（防写者饥饿）

```cpp
#include <mutex>
#include <condition_variable>

class RWLock {
public:
    void lock_read() {
        std::unique_lock<std::mutex> lk(mtx_);
        // 有写者等待时，新读者必须等待——防止写者饥饿
        read_cv_.wait(lk, [this] {
            return waiting_writers_ == 0 && active_writers_ == 0;
        });
        ++active_readers_;
    }

    void unlock_read() {
        std::unique_lock<std::mutex> lk(mtx_);
        if (--active_readers_ == 0)
            write_cv_.notify_one();
    }

    void lock_write() {
        std::unique_lock<std::mutex> lk(mtx_);
        ++waiting_writers_;  // 登记，阻止新读者插队
        write_cv_.wait(lk, [this] {
            return active_readers_ == 0 && active_writers_ == 0;
        });
        --waiting_writers_;
        ++active_writers_;
    }

    void unlock_write() {
        std::unique_lock<std::mutex> lk(mtx_);
        --active_writers_;
        if (waiting_writers_ > 0)
            write_cv_.notify_one();   // 优先唤醒等待写者
        else
            read_cv_.notify_all();    // 没有等待写者，批量唤醒读者
    }

private:
    std::mutex              mtx_;
    std::condition_variable read_cv_;
    std::condition_variable write_cv_;
    int active_readers_  = 0;
    int active_writers_  = 0;
    int waiting_writers_ = 0;  // 防饥饿关键
};
```

### 为什么用 unique_lock 而非 lock_guard

`condition_variable::wait()` 内部需要 `lk.unlock()` → 挂起 → `lk.lock()`，要求 `unique_lock` 暴露的手动 lock/unlock 接口；`lock_guard` 是纯 RAII，无 `unlock()` 方法，不可用。

### 与 std::shared_mutex 的差异

| | 本实现 | std::shared_mutex |
|--|--------|-------------------|
| 写者饥饿防御 | 显式 `waiting_writers_` 计数 | 实现定义，无保证 |
| 性能 | 每次加解锁获取 mutex | futex 优化，无竞争路径更快 |
| 超时支持 | 需自己加 `wait_for` | 原生 `try_lock_shared_for` |
| 可调度策略 | 可自定义 | 不可控 |

---

## LRU 缓存（单锁 → 分片锁 → 近似无锁）

### 单锁版 O(1) get/put

```cpp
template<typename K, typename V>
class LRUCache {
public:
    explicit LRUCache(size_t capacity) : capacity_(capacity) {}

    std::optional<V> get(const K& key) {
        std::lock_guard<std::mutex> lk(mtx_);
        auto it = map_.find(key);
        if (it == map_.end()) return std::nullopt;
        list_.splice(list_.begin(), list_, it->second);
        return it->second->second;
    }

    void put(const K& key, const V& value) {
        std::lock_guard<std::mutex> lk(mtx_);
        auto it = map_.find(key);
        if (it != map_.end()) {
            it->second->second = value;
            list_.splice(list_.begin(), list_, it->second);
            return;
        }
        if (map_.size() >= capacity_) {
            auto last = std::prev(list_.end());
            map_.erase(last->first);
            list_.erase(last);
        }
        list_.emplace_front(key, value);
        map_[key] = list_.begin();
    }

private:
    size_t capacity_;
    std::mutex mtx_;
    std::list<std::pair<K, V>> list_;
    std::unordered_map<K, typename std::list<std::pair<K,V>>::iterator> map_;
};
```

### 高并发版：分片锁（生产首选）

```cpp
template<typename K, typename V, size_t SHARDS = 16>
class ShardedLRUCache {
public:
    explicit ShardedLRUCache(size_t total_capacity)
        : shard_capacity_((total_capacity + SHARDS - 1) / SHARDS) {
        for (auto& s : shards_)
            s = std::make_unique<LRUCache<K,V>>(shard_capacity_);
    }

    std::optional<V> get(const K& key) { return shard(key).get(key); }
    void put(const K& key, const V& value) { shard(key).put(key, value); }

private:
    LRUCache<K,V>& shard(const K& key) {
        // SHARDS 取 2 的幂次可用位运算替代取模
        return *shards_[std::hash<K>{}(key) % SHARDS];
    }
    size_t shard_capacity_;
    std::array<std::unique_ptr<LRUCache<K,V>>, SHARDS> shards_;
};
```

### 近似无锁版：原子时间戳（Redis 同款）

核心优化：`get` 的热度更新从"链表 splice（需写锁）"变成"原子写时间戳（无锁）"。

```cpp
struct Entry {
    V value;
    std::atomic<uint64_t> last_access;  // 原子更新，无需写锁
};

std::optional<V> get(const K& key) {
    std::shared_lock lk(mtx_);          // 读锁并发度高
    auto it = map_.find(key);
    if (it == map_.end()) return std::nullopt;
    it->second.last_access.store(now(), std::memory_order_relaxed);
    return it->second.value;
}
// 淘汰时随机采样 N 个，驱逐最老的（近似 LRU，非严格）
```

### 方案对比

| 方案 | 严格 LRU | get 并发度 | 复杂度 | 推荐场景 |
|------|---------|-----------|--------|---------|
| 单锁 | 是 | 低 | 低 | 低并发 |
| 分片锁 | 是 | 中（shard 级） | 低 | **高并发首选** |
| 近似 LRU + 时间戳 | 近似 | **高**（shared_lock） | 低 | 读多写少，Redis 同款 |
| Hazard Pointer | 是 | 极高 | 极高 | 不推荐手写 |

---

## A10. fd 耗尽与预防

### EMFILE vs ENFILE

| 错误码 | 含义 | 限制范围 |
|--------|------|---------|
| `EMFILE` | 当前进程 fd 数量达到上限 | 进程级 |
| `ENFILE` | 系统 fd 总数达到上限 | 系统级 |

### ulimit -n vs fs.file-max

| 参数 | 控制范围 | 是否需要重启进程 |
|------|---------|---------------|
| `ulimit -n` | 当前进程的 fd 上限 | **需要**，已运行进程继承旧 limit |
| `fs.file-max` | 整个系统 fd 总数上限 | 不需要，立即生效 |
| `fs.nr_open` | 单进程 fd 的内核硬上限（ulimit 不能超过它） | 不需要 |

正确调整顺序：先调 `fs.nr_open` → 再调 `fs.file-max` → 再改 `limits.conf` → 重启服务进程。

### 主动预防手段

**监控预警**
```cpp
int get_open_fd_count() {
    int count = 0;
    DIR* dir = opendir("/proc/self/fd");
    if (!dir) return -1;
    while (readdir(dir)) ++count;
    closedir(dir);
    return count - 3;
}
```

**哨兵 fd 优雅降级**（Nginx / libevent 经典技巧）
- 提前 `open("/dev/null")` 占一个 fd 作为哨兵
- `accept` 返回 `EMFILE` 时，关闭哨兵 fd → accept 成功 → 向客户端返回 503 → 重新打开哨兵 fd
- 避免 listen fd 反复触发可读事件导致 CPU 空转

**RAII + O_CLOEXEC 防泄漏**
```cpp
struct FdGuard {
    int fd;
    explicit FdGuard(int f) : fd(f) {}
    ~FdGuard() { if (fd >= 0) close(fd); }
    FdGuard(const FdGuard&) = delete;
};
// socket(..., SOCK_CLOEXEC) 防 fork 后子进程继承 fd
```

---

## C3. gRPC 四种流模式

### 选型速查

| 模式 | 适用场景 | 不可替代的理由 |
|------|---------|--------------|
| Unary | 普通请求-响应 | — |
| Server Streaming | 大数据导出、实时日志推送、进度通知 | 结果集大或持续产生，首字节延迟低 |
| Client Streaming | 日志批量上报、大文件分片上传、传感器数据采集 | 单连接持续发送，节省连接开销，支持背压 |
| Bidirectional | 实时对话、游戏状态同步、音视频信令 | 双方都需主动推送且延迟敏感，Unary 轮询不可替代 |

### Client Streaming vs 批量 Unary

| | 批量 Unary | Client Streaming |
|--|-----------|-----------------|
| 连接开销 | N 次 RPC | 1 条连接 |
| 内存 | 需先攒够再发 | 边产生边发 |
| 背压 | 无 | 有（流控） |

### Bidirectional Streaming 判断标准

> 如果双方都需要**主动推送**且对延迟敏感，Unary 轮询无法满足，Bidirectional Streaming 是唯一选择。

---

## E9. 同步数据库驱动阻塞事件循环

### 本质问题

epoll 单线程 Reactor 要求**事件循环线程永远不能阻塞**。同步 `conn->query()` 独占线程，期间所有连接的 IO 事件全部积压。

**误区：** ET + O_NONBLOCK 只解决网络 IO 就绪通知问题，无法解决同步库内部阻塞线程的问题。MySQL Connector/C++ 是同步库，修改底层 fd 属性会导致其报错或死循环。

**Redis 缓存是补充优化，不是根本解法**——cache miss 时同样的阻塞问题仍然存在。

### 三种解法

| 方案 | 核心思路 | 代价 |
|------|---------|------|
| **线程池卸载** | 查询任务投递线程池，完成后通过 `eventfd` 通知事件循环 | 线程切换开销，回调地狱 |
| **协程（C++20 / libco）** | `co_await db.query()` 挂起协程，线程继续处理其他协程，响应到来后恢复 | 需要异步驱动支持 |
| **异步 MySQL 驱动** | 将 MySQL socket 注册进 epoll，像普通网络 IO 一样处理 | 驱动成熟度低 |

### 线程池 vs 协程的本质区别

- **线程池**：阻塞发生在工作线程，事件循环不阻塞，但有线程切换和同步开销
- **协程**：阻塞转化为协程挂起，整个线程继续跑其他协程，**零线程切换**，但需要异步底层支持

```
// 线程池方案
epoll 线程收到请求
  → 投递 DB 查询到线程池（立刻返回继续处理其他事件）
  → 线程池工作线程执行同步 query
  → 完成后 eventfd 通知事件循环
  → 事件循环线程回调业务逻辑，发送响应

// 协程方案
co_await db.async_query(sql);
// 编译器展开为：挂起当前协程 → 事件循环处理其他协程 → 响应到来恢复协程
// 代码看起来是同步，实际是异步，零线程切换
```
