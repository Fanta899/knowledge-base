## DPDK Hugepage vs mmap Hugepage 区别

### 1. 底层机制

| 特性 | mmap Hugepage | DPDK Hugepage |
|------|---------------|---------------|
| 实现方式 | 直接使用 Linux 内核的 hugetlbfs | 基于 hugetlbfs 构建的用户态内存管理 |
| 管理层 | 内核管理 | DPDK EAL (Environment Abstraction Layer) 管理 |
| 虚拟地址 | 由内核分配 | DPDK 可控制虚拟地址布局 |

### 2. 使用方式

**mmap Hugepage:**
```cpp
// 方式1: 使用 MAP_HUGETLB flag
void* ptr = mmap(NULL, size, PROT_READ|PROT_WRITE, 
                 MAP_SHARED|MAP_HUGETLB, -1, 0);

// 方式2: 挂载 hugetlbfs 后映射文件
int fd = open("/mnt/huge/myfile", O_CREAT|O_RDWR, 0755);
void* ptr = mmap(NULL, size, PROT_READ|PROT_WRITE, MAP_SHARED, fd, 0);
```

**DPDK Hugepage:**
```cpp
// DPDK 初始化时自动配置
rte_eal_init(argc, argv);
// 分配内存
void* ptr = rte_malloc("name", size, alignment);
// 或使用 mempool
struct rte_mempool* pool = rte_mempool_create(...);
```

### 3. 关键区别

| 方面 | mmap Hugepage | DPDK Hugepage |
|------|---------------|---------------|
| **物理地址访问** | 无法直接获取 | 提供 `rte_mem_virt2phy()` 获取物理地址 |
| **IOVA 连续性** | 不保证 | 可配置 IOVA 连续内存用于 DMA |
| **NUMA 感知** | 需手动处理 | 内置 NUMA 感知分配 |
| **内存池** | 无 | 提供 mempool 管理 |
| **多进程共享** | 需自己管理 | 原生支持主/从进程模式 |
| **内存碎片** | 可能碎片化 | 内置内存分配器减少碎片 |

### 4. 适用场景

**mmap Hugepage 适合:**
- 简单的大内存分配
- 不需要 DMA 的场景
- 不依赖 DPDK 的应用

**DPDK Hugepage 适合:**
- 高性能网络 I/O（需要物理地址给网卡 DMA）
- 需要 NUMA 优化
- 多进程共享 NIC 队列
- 零拷贝数据包处理

### 5. 性能对比

两者在 **TLB miss 减少**方面效果相同（都是 huge page），但 DPDK 额外提供：
- 预分配和对齐优化
- 物理地址连续性保证（对 DMA 关键）
- 与 DPDK poll-mode 驱动的无缝集成

**总结**：DPDK hugepage 是在 Linux hugetlbfs 之上的高级封装，专为高性能数据面应用优化，特别是需要与硬件 DMA 交互的场景。

## 问题：使用int mflags = MAP_SHARED | MAP_LOCKED; 涉及到mmap hugepage分配吗？

```cpp
int mflags = MAP_SHARED | MAP_LOCKED;
```

**不涉及 hugepage 分配**。

## 当前使用的标志

| 标志 | 作用 |
|------|------|
| `MAP_SHARED` | 映射可被多个进程共享 |
| `MAP_LOCKED` | 锁定页面在物理内存中，防止被交换到 swap |

## 如果要使用 hugepage

需要添加 `MAP_HUGETLB` 标志：

```cpp
// 使用默认大小的 hugepage (通常是 2MB)
int mflags = MAP_SHARED | MAP_LOCKED | MAP_HUGETLB;

// 或者指定 hugepage 大小 (Linux 3.8+)
int mflags = MAP_SHARED | MAP_LOCKED | MAP_HUGETLB | MAP_HUGE_2MB;  // 2MB
int mflags = MAP_SHARED | MAP_LOCKED | MAP_HUGETLB | MAP_HUGE_1GB;  // 1GB
```

## 为什么当前代码不使用 hugepage？

可能的原因：

1. **共享内存大小** - `ShmBlock` 结构体可能不够大，使用 hugepage (最小 2MB) 会浪费内存
2. **系统配置复杂性** - hugepage 需要预先配置 (nr_hugepages)
3. **兼容性** - 不是所有部署环境都配置了 hugepage
4. **`MAP_LOCKED` 已足够** - 对于 PM counters 这种场景，锁定在物理内存已能满足性能需求

## 性能对比

| 特性 | 当前代码 (4KB 页) | 使用 hugepage (2MB 页) |
|------|-------------------|------------------------|
| TLB 效率 | 较低 | 高 (减少 TLB miss) |
| 内存浪费 | 低 | 可能较高 (最小 2MB 粒度) |
| 配置要求 | 无 | 需要预留 hugepage |
| 页面锁定 | ✅ (MAP_LOCKED) | ✅ (hugepage 天然锁定) |

## 那是在使用了哪块memory ？
Optimizing tool selection...当前代码使用的是 **POSIX 共享内存 (tmpfs)**，而不是 hugepage。

## 内存来源分析

从代码可以看到完整的流程：

```cpp
// 1. 打开/创建 POSIX 共享内存对象
fd = shm_open(sharedMemoryName, oflag, 0600);

// 2. 设置大小
ftruncate(fd, shMemLen);

// 3. 映射到进程地址空间
shmBlock = reinterpret_cast<ShmBlock*>(mmap(nullptr, shMemLen, prot, mflags, fd, offset));
```

## 实际使用的内存类型

| 层级 | 说明 |
|------|------|
| **文件系统** | shm (tmpfs - 基于内存的文件系统) |
| **物理内存** | 普通 4KB 页面 (anonymous pages) |
| **虚拟内存** | 通过 mmap 映射到进程地址空间 |

## 内存位置

```
┌─────────────────────────────────────────────────────────┐
│                    物理内存 (RAM)                        │
│  ┌─────────────────────────────────────────────────┐   │
│  │              tmpfs (/dev/shm)                    │   │
│  │  ┌─────────────────────────────────────────┐    │   │
│  │  │  /dev/shm/<sharedMemoryName>            │    │   │
│  │  │  (实际的 ShmBlock 数据)                   │    │   │
│  │  └─────────────────────────────────────────┘    │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
           ↑
           │ mmap (MAP_SHARED | MAP_LOCKED)
           ↓
┌─────────────────────────────────────────────────────────┐
│              进程虚拟地址空间                             │
│  ┌─────────────────────────────────────────────────┐   │
│  │  shmBlock 指针指向的映射区域                      │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

## 关键特点

| 特性 | 说明 |
|------|------|
| **存储位置** | RAM (不是磁盘) |
| **页面大小** | 4KB (标准页面) |
| **持久性** | 系统重启后丢失 |
| **可共享** | 多进程可通过相同名称访问 |
| **锁定** | `MAP_LOCKED` 防止被 swap out |

## 验证方法

在系统上可以查看：

```bash
# 查看 /dev/shm 中的共享内存文件
ls -la /dev/shm/

# 查看 tmpfs 挂载信息
df -h /dev/shm

# 查看进程的内存映射
cat /proc/<pid>/maps | grep shm
```

## 与其他内存类型的对比

| 内存类型 | 来源 | 页面大小 | 用途 |
|----------|------|----------|------|
| **当前代码 (shm_open + mmap)** | tmpfs (/dev/shm) | 4KB | 进程间共享数据 |
| DPDK hugepage | hugetlbfs | 2MB/1GB | 高性能网络 I/O |
| mmap + MAP_HUGETLB | hugetlbfs | 2MB/1GB | 需要大页的应用 |
| malloc/new | heap (brk/mmap) | 4KB | 普通堆分配 |
