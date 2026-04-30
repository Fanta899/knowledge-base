# A 类：网络 I/O 模型与事件驱动

---

## A1. epoll ET vs LT，ET 必须循环读到 EAGAIN

### 核心区别

| 模式 | 触发条件 | 编程难度 |
|------|---------|---------|
| LT（水平触发） | 缓冲区有数据就持续触发 | 低，不读完下次仍通知 |
| ET（边缘触发） | 缓冲区水位上升时触发一次 | 高，必须一次读完 |

### ET 必须循环读到 EAGAIN 的原因

ET 只在"新数据写入内核缓冲区"时触发一次。若不读完，剩余数据无新数据到来时永久滞留，导致漏读。

**正确读取姿势：**
```cpp
while (true) {
    ssize_t n = read(fd, buf, sizeof(buf));
    if (n > 0) {
        // 处理数据
    } else if (n == 0) {
        close(fd); break;           // 对端关闭
    } else {
        if (errno == EAGAIN || errno == EWOULDBLOCK)
            break;                  // 数据读完，退出
        break;                      // 真实错误
    }
}
```

### ET 的强制要求

- **必须使用非阻塞 socket**：阻塞 socket 在数据读完后，下一次 `read()` 会永久阻塞，挂死整个事件循环线程。

### 常见误区

- ET 不是"从无到有才触发"，而是"缓冲区水位上升"即触发
- LT 对写事件（`EPOLLOUT`）忘记在写完后删除，导致 `epoll_wait` 持续空转，CPU 飙升

### 加分点

- Nginx 使用 ET + 非阻塞，配合 `EPOLLONESHOT` 保证同一 fd 在多线程场景下不被多个线程同时处理

---

## A2. io_uring vs epoll，SQ/CQ 环形队列

### 本质差异

| 维度 | epoll | io_uring |
|------|-------|----------|
| 模型 | 就绪通知（readiness） | 完成通知（completion） |
| 数据路径 | 内核通知就绪，用户再调用 read/write | 内核直接将数据读入用户 buffer，完成后写 CQE |
| 系统调用 | 每次 read/write 各一次 | 批量提交一次，甚至零调用（SQPOLL） |
| 共享机制 | 无 | mmap 共享内存 + 无锁环形队列（SQ/CQ） |

### SQ/CQ 工作流

```
用户态：填写 SQE → 移动 SQ tail（纯内存写）
内核态：消费 SQE → 执行 IO → 写 CQE → 移动 CQ head
用户态：检查 CQ head → 处理完成事件
```

- SQ/CQ 通过 **mmap 共享内存 + 无锁环形队列** 实现零系统调用写入
- CQ 里存的是**完成状态**（res 字段），不是数据本身；数据在预注册的用户 buffer 里

### SQPOLL 模式

内核起一个专属 kthread 持续轮询 SQ，用户侧写入 SQE 后无需任何系统调用。

**代价：** kthread 持续空转，占用一个 CPU 核心；空闲超时后线程睡眠，需 `io_uring_enter()` 唤醒。

**适合场景：** 极高 QPS、IO 密集、对延迟极度敏感的服务（NVMe 存储引擎、网络包转发）。

### 相关库

- **liburing**：io_uring 官方用户态封装，把底层系统调用封装成简洁 API
- **Seastar**：Share-nothing 架构，每核绑定一个线程 + io_uring 实例，ScyllaDB 使用，P99 延迟可达微秒级

### 加分点

- `io_uring_register` 可预注册 fd 和 buffer，避免每次 IO 的内核引用计数开销
- linked SQE（`IOSQE_IO_LINK`）可串联多个操作为依赖链

---

## A3. Reactor vs Proactor，Linux 下用 Reactor 模拟 Proactor

### 核心区别

| 维度 | Reactor | Proactor |
|------|---------|----------|
| 通知时机 | IO **就绪**时通知 | IO **完成**时通知 |
| 数据搬运 | 用户负责调用 read/write | 内核/框架负责，用户拿到结果 |
| 典型实现 | epoll / select / poll | io_uring / Windows IOCP |

### Boost.Asio 模拟 Proactor 的流程

```
async_read(socket, buf, handler)
    → 向 epoll 注册 EPOLLIN
epoll_wait 返回可读
    → Asio 内部调用 read() 读入 buf
    → 数据满足条件后投递 completion handler
```

**性能影响：** 相比真正的 Proactor（io_uring），多了一次 `read()` 系统调用和一次内核→用户的拷贝。

### 加分点

- Boost.Asio 1.84+ 支持 io_uring 后端（`BOOST_ASIO_HAS_IO_URING`），此时才是真正的 Proactor
- Windows IOCP 是原生 Proactor

---

## A4. 多 Reactor 多线程模型（主从 Reactor）

### 架构

```
Main Reactor（1个）
  → epoll_wait 监听 listen fd
  → accept() → 轮询分发给 Sub Reactor

Sub Reactor 0 / 1 / 2 ...（独立线程+epoll）
  → 负责 conn fd 的 IO 读写
  → 同一连接绑定同一 Sub Reactor，天然串行无锁

业务线程池（可选）
  → Sub Reactor 读完数据后投递，避免阻塞 IO 循环
```

### 分工依据

- Main Reactor 专注 accept：操作轻量，混入 IO 处理会导致新连接建立抖动
- Sub Reactor 专注 IO：固定连接集合，同一连接串行处理，无锁
- 跨线程唤醒：通过 `eventfd` 实现，Main Reactor 投递新连接任务时写入 eventfd 触发唤醒

### 惊群演进路径

```
多线程 accept() 惊群
  → Linux 2.6 修复（只唤醒一个 accept）
  → 转移至 epoll_wait() 惊群（多线程共享 epoll + listen fd）
  → EPOLLEXCLUSIVE 限制唤醒（Linux 4.5）
  → SO_REUSEPORT 彻底消除（各自独立 listen socket）
```

### EPOLLONESHOT 的用途

解决**同一 conn fd 被多个线程同时处理**的问题（与惊群无关）：处理完后需 `epoll_ctl(EPOLL_CTL_MOD)` 重新 arm。

---

## A5. SO_REUSEPORT，内核负载均衡与惊群

### 分发机制

```
hash(src_ip, src_port, dst_ip, dst_port) % num_sockets
```

- 同一客户端的连接总落到同一 worker（连接亲和性）
- **普通取模 hash**，不是一致性 hash

### 已知问题

| 问题 | 原因 | 缓解方案 |
|------|------|---------|
| Worker 增减时新连接分布变化 | 取模 N 变化导致 hash 结果全变 | 接受短暂不均衡 |
| Reload 时存量长连接断开 | 旧 worker 退出关闭 socket | 优雅关闭：停止 accept，等连接耗尽再退出 |

### SO_REUSEPORT vs EPOLLEXCLUSIVE

| | EPOLLEXCLUSIVE | SO_REUSEPORT |
|---|---|---|
| listen socket | 共享同一个 | 各自一个 |
| 解决方式 | 限制唤醒数量 | 内核直接分发到不同 socket |
| 负载均衡 | 非均匀 | 均匀（四元组 hash） |

### 加分点

- Linux 4.7+ 支持挂载 eBPF 程序自定义分发策略（`BPF_PROG_TYPE_SK_REUSEPORT`），实现加权轮询或一致性 hash
- `SO_REUSEADDR`：只允许 TIME_WAIT 状态下重用端口，与 `SO_REUSEPORT` 是不同功能

---

## A6. 非阻塞 connect 实现流程

### 完整流程

```cpp
// 1. 创建非阻塞 socket
int fd = socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK, 0);

// 2. 发起连接，立即返回 EINPROGRESS
int ret = connect(fd, (sockaddr*)&addr, sizeof(addr));
if (ret < 0 && errno != EINPROGRESS) { close(fd); return; }
if (ret == 0) { on_connected(fd); return; }  // 极少数直接成功

// 3. 注册 EPOLLOUT，等待握手完成
epoll_ctl(epoll_fd, EPOLL_CTL_ADD, fd, &ev);  // ev.events = EPOLLOUT|EPOLLERR

// 4. epoll_wait 返回后用 getsockopt 确认结果
int err = 0; socklen_t len = sizeof(err);
getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &len);
if (err == 0) {
    on_connected(fd);
    // 改为监听 EPOLLIN
} else {
    close(fd); on_connect_failed(err);
}
```

### 关键点

- 连接成功和失败都会触发 `EPOLLOUT`，必须用 **`getsockopt(SO_ERROR)`** 区分
- `err == 0`：成功；`err == ECONNREFUSED/ETIMEDOUT`：失败
- 只检查 `EPOLLERR` 不够，失败有时只触发 `EPOLLOUT`

### 事件驱动的本质

非阻塞编程将一个完整操作拆成多个回调，由事件循环串联驱动，一个线程处理成千上万连接。这是**回调地狱**的来源，也是 C++20 协程要解决的问题。

### 加分点

- 连接池批量建连时，非阻塞 connect 可并发发起所有连接，只等最慢的一条
- 需配合**超时定时器**，握手超时后主动 close 并标记节点不可用

---

## A7. 应用层心跳 vs TCP Keepalive

### 核心区别

| 维度 | TCP Keepalive | 应用层心跳 |
|------|--------------|-----------|
| 工作层次 | 传输层（L4） | 应用层（L7） |
| 能检测的故障 | 网络断开、对端崩溃 | 含上层服务假死、线程池耗尽 |
| 参数控制粒度 | 系统全局 | 每条连接独立配置 |
| 默认探测间隔 | 7200s（不可用） | 自定义（5~30s） |
| 携带业务信息 | 不能 | 可以（版本、负载、时钟同步） |
| 穿越 NAT/代理 | 可能被过滤 | 正常业务数据，不被过滤 |

### TCP Keepalive 的根本局限

TCP 连接活着 ≠ 服务正常。典型场景：服务端线程池全部死锁，TCP 探测正常回 ACK，但业务请求无响应。应用层 PING 超时可发现此问题。

### 最佳实践

两者互补：TCP Keepalive 兜底处理内核级连接泄漏，应用层心跳处理业务级服务不可用。gRPC 同时使用两者。

---

## A8. SIGPIPE 信号

### 触发条件（两步）

```
第一次 write → 对端已关闭 → 收到 RST → 返回 -1, errno=ECONNRESET
第二次 write → 再次写已收到 RST 的 socket → 内核发送 SIGPIPE
```

### 为何必须忽略

`SIGPIPE` 默认行为是**终止进程**。某个客户端断开时，服务端若在写响应触发 SIGPIPE，整个进程被杀死，所有正常客户端中断。

### 处理方式

```cpp
// 方式1：全局忽略（推荐，启动时设置一次）
signal(SIGPIPE, SIG_IGN);

// 方式2：每次 send 加 flag（只对本次有效）
send(fd, buf, len, MSG_NOSIGNAL);
```

忽略后 write/send 返回 `-1, errno=EPIPE`，程序正常处理。

### 常见误区

- 误以为第一次写就触发——实际需要**两次写**才触发信号
- 只设置 `MSG_NOSIGNAL` 但某处用了 `write()`，仍会产生 SIGPIPE

---

## A9. 粘包与拆包，TLV 协议设计

### 根本原因

TCP 是字节流协议，无消息边界，多次 `send()` 可能被合并为一次 `recv()`，一次 `send()` 也可能被拆成多次 `recv()`。

### 三种分包方案

| 方案 | 缺陷 | 典型应用 |
|------|------|---------|
| 固定长度 | 无法变长，浪费空间 | 极少使用 |
| 分隔符 | 不支持二进制数据 | HTTP 请求头、Redis RESP2 |
| TLV 长度字段 | 需防超大包内存攻击 | gRPC、Kafka、自研 RPC |

### TLV 安全实现

```cpp
uint32_t body_len = ntohl(raw_len);        // 网络字节序转换（大端）
if (body_len > MAX_MSG_SIZE) {             // 必须做上限校验！防内存攻击
    close_connection(fd); return;
}
std::vector<char> body(body_len);
```

### 为何用大端（网络字节序）

RFC 1700 规定"网络字节序"为大端，所有 IP/TCP/UDP 协议头遵循此约定。不同架构机器（x86 小端、SPARC 大端）通信时各自调用 `htonl()`/`ntohl()` 转换。

### Scatter-Gather IO：readv/writev

```cpp
struct iovec iov[2];
iov[0] = {&header, sizeof(header)};
iov[1] = {body.data(), body.size()};
readv(fd, iov, 2);   // 一次系统调用，数据直接散落到两块内存
```

**优势：** 一次系统调用 + 无额外内存拷贝，Nginx 的响应发送大量使用 `writev`。

---

## A10. fd 耗尽预防

### 耗尽时的现象

- `accept()` 返回 -1，`errno = EMFILE`（进程超限）或 `ENFILE`（系统超限）
- LT 模式下若只 `continue` 跳过：epoll_wait 空转，CPU 100%，所有连接饿死

### 两个限制的区别

| 参数 | 作用范围 | 配置方式 |
|------|---------|---------|
| `ulimit -n` | 单个进程最多打开的 fd 数 | `/etc/security/limits.conf` |
| `fs.file-max` | 整个系统所有进程 fd 总数 | `/etc/sysctl.conf` |

### EMFILE 正确处理（Muduo 方案）

```cpp
// 启动时预占一个空闲 fd
int idle_fd_ = open("/dev/null", O_RDONLY);

// accept 循环中
if (errno == EMFILE) {
    close(idle_fd_);                    // 腾出一个槽
    conn_fd = accept(listen_fd, ...);   // accept 后立即关闭，优雅拒绝客户端
    close(conn_fd);                     // 发送 RST，让客户端感知
    idle_fd_ = open("/dev/null", O_RDONLY);  // 重新占位
}
```

### 生产预防策略

1. 启动脚本设置 `ulimit -n 1000000`，`limits.conf` 持久化
2. Prometheus 监控 `/proc/self/fd` 目录下 fd 数量，超 80% 告警
3. 连接池限制最大连接数，防连接泄漏耗尽 fd

---

## A11. TCP 三次握手/四次挥手，TIME_WAIT ⭐⭐

### 三次握手状态机

```
客户端                        服务端
CLOSED                        LISTEN
  │── SYN(seq=x) ───────────► SYN_RECV
  │◄── SYN+ACK(seq=y,ack=x+1) ─│
ESTABLISHED ◄──────────────── │
  │── ACK(ack=y+1) ───────────► ESTABLISHED
```

**为何三次不是两次：** 两次无法验证客户端接收能力；历史延迟 SYN 会导致服务端误建连接。

### 四次挥手状态机（客户端主动关闭）

```
客户端          服务端
  │── FIN ──────► CLOSE_WAIT
FIN_WAIT_1       │（服务端处理剩余数据）
  │◄── ACK ──── │
FIN_WAIT_2       │── FIN ──► LAST_ACK
TIME_WAIT ◄──── │
  │── ACK ──────► CLOSED
（等 2MSL 后）
CLOSED
```

### TIME_WAIT 持续 2MSL 的两个原因

1. **保证最后 ACK 可达**：若最后 ACK 丢失，服务端重传 FIN，客户端在 TIME_WAIT 期间可重发 ACK
2. **让旧报文在网络中消亡**：防止延迟报文污染同四元组的新连接

### 服务器大量 TIME_WAIT 优化

**参数级：**
```bash
net.ipv4.tcp_tw_reuse=1       # 允许 TIME_WAIT socket 在安全条件下复用
net.ipv4.ip_local_port_range="1024 65535"
# 注意：tcp_tw_recycle 已被移除，禁止使用
```

**架构级（更推荐）：**
- 短连接改长连接（HTTP Keep-Alive、连接池）
- 尽量让客户端主动关闭
- 网关层连接复用

### 常见误区

- 看到 TIME_WAIT 多就改内核参数，治标不治本
- 误以为 TIME_WAIT 是异常状态，实际是 TCP 可靠关闭机制的一部分

---

## A12. 半连接队列/全连接队列，SYN Flood ⭐

### 两个队列位置

```
客户端 ──SYN──► [SYN queue] 状态：SYN_RECV
客户端 ──ACK──► 移入 [accept queue] 状态：ESTABLISHED
应用 accept() 取走连接
```

- **SYN queue**：大小由 `net.ipv4.tcp_max_syn_backlog` 控制
- **accept queue**：大小由 `min(backlog, net.core.somaxconn)` 控制

### SYN Flood 与 tcp_syncookies

**攻击原理：** 大量伪造源 IP 的 SYN 包打满 SYN queue，合法连接被丢弃。

**tcp_syncookies 防御：** SYN queue 满时，将连接信息编码进 SYN+ACK 的 ISN，无需分配队列资源。合法客户端回 ACK 时内核解码验证直接建连。

**代价：** 无法支持 TCP 选项（SACK、Window Scale），大流量下性能略降。

### 排查命令

```bash
# 全连接队列溢出
ss -lnt          # Recv-Q 接近 Send-Q 说明队列满
netstat -s | grep "listen queue"   # 溢出累计次数

# 半连接队列溢出
ss -ant | grep SYN-RECV | wc -l
netstat -s | grep "SYNs to LISTEN"
```

### 常见误区

- `backlog` 参数控制的是**全连接队列**上限，不是半连接队列
- 全连接队列满时内核**静默丢弃**，应用层完全感知不到

---

## A13. TCP Nagle 算法与延迟 ACK 的 40ms 陷阱

### Nagle 算法

**目的：** 减少网络中小包数量。

**发送条件（满足任意一个才发）：**
1. 待发数据 ≥ MSS
2. 所有已发数据都已收到 ACK

### 延迟 ACK

收到数据后等待最多 **40ms**，期望把 ACK 捎带在回复数据里一起发。

### 两者叠加产生 40ms 延迟

```
客户端                              服务端
  │── write(header，小包) ─────────► 延迟 ACK 定时器启动（等40ms）
  │
  │ Nagle：已有在途未确认数据，body 被憋住
  │
  │              ← 双方互相等待 →
  │
  │                          40ms 后延迟 ACK 超时
  │◄── ACK ────────────────────── │
  │── write(body) ──────────────► │
```

**根本原因：** 客户端 Nagle 等 ACK，服务端延迟 ACK 等捎带，互相等对方。

### 解决方案：TCP_NODELAY

```cpp
int flag = 1;
setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &flag, sizeof(flag));
```

禁用 Nagle 算法，每次 write 立即发包。RPC/实时服务必须设置。

### 常见误区

- 只设客户端 `TCP_NODELAY`，服务端回包仍可能被 Nagle 憋住
- `TCP_CORK`（主动攒包）与 `TCP_NODELAY`（禁止内核自动攒包）是不同机制

### 加分点

- `TCP_CORK` + 关闭时立即冲刷：Nginx 的 `tcp_nopush` 原理

---

## A14. TCP Keepalive 三参数，连接泄漏检测 ⭐

### 三个参数

| 参数 | 含义 | 默认值 | 生产推荐 |
|------|------|--------|---------|
| `tcp_keepalive_time` | 空闲多久后开始探测 | 7200s | 60~120s |
| `tcp_keepalive_intvl` | 探测包间隔 | 75s | 10~20s |
| `tcp_keepalive_probes` | 连续失败多少次后断开 | 9次 | 3~5次 |

默认配置：死连接最长 **2小时以上** 才被检测到，生产不可用。

### Per-socket 设置

```cpp
int keepalive = 1;
setsockopt(fd, SOL_SOCKET, SO_KEEPALIVE, &keepalive, sizeof(keepalive));

int time = 60, intvl = 10, probes = 3;
setsockopt(fd, IPPROTO_TCP, TCP_KEEPIDLE,  &time,   sizeof(time));
setsockopt(fd, IPPROTO_TCP, TCP_KEEPINTVL, &intvl,  sizeof(intvl));
setsockopt(fd, IPPROTO_TCP, TCP_KEEPCNT,   &probes, sizeof(probes));
```

### 连接泄漏完整检测方案（四层）

1. **TCP Keepalive**：兜底处理网络层死连接
2. **应用层空闲超时**：定时扫描 `last_active` 时间戳，超时主动关闭
3. **借出超时强制回收**：连接池记录借出时间 + 调用栈，超 `max_lease_time` 强制回收并打印泄漏日志
4. **Prometheus 监控**：暴露 active/idle/leaked 连接数指标

### 常见误区

- 只改 sysctl 全局参数，忘记用 `setsockopt` per-socket 设置
- 以为 Keepalive 能检测应用层假死——不能，需要应用层心跳补充
- 连接池只检测空闲连接，不检测借出超时——长时间借出的泄漏连接无法被发现

### 加分点

- 连接泄漏最根本的预防是 **RAII**：`std::unique_ptr` + 自定义 deleter 包装连接对象，析构时自动归还连接池
