在 Linux 系统中，大页（HugePages） 是一项优化内存管理性能的关键技术。简单来说，它就像是把原本琐碎的“零钱”换成“大钞”，从而减少管理上的开销。

## 1. 为什么需要 HugePages？
在现代计算机架构中，CPU 通过 MMU（内存管理单元） 将虚拟地址转换为物理地址。这个映射关系存储在 页表（Page Table） 中。

* 标准页（Standard Pages）： 默认大小通常是 4KB。
* 痛点： 随着服务器内存增加（如 128GB 或更高），4KB 的页会导致页表变得非常庞大。
* TLB 瓶颈： CPU 缓存了一部分页表项，称为 TLB (Translation Lookaside Buffer)。如果内存很大且全是 4KB 的小页，TLB 经常会发生缺失（Miss），导致 CPU 必须频繁访问慢速的主存来查找地址映射，性能大打折扣。

## 2. HugePages 的核心优势
通过将页面大小从 4KB 增加到 2MB 甚至 1GB，HugePages 带来了以下好处：

* 减少 TLB 缺失： 一个 2MB 的大页涵盖了 512 个小页的范围，显著提高了 TLB 命中率。
* 减少页表开销： 页表项减少，占用的内存也随之减少。
* 内存不被交换（Swap）： HugePages 在 Linux 中通常被锁定在物理内存中，不会被系统交换到磁盘上。这对于要求高延迟稳定性的应用（如数据库）至关重要。

## 3. 两种主要的实现方式
A. 静态大页 (Static HugePages)

* 原理： 在系统启动时或运行初期手动预分配。
* 优点： 性能最稳定，完全掌控。
* 缺点： 灵活性差，预分配后即使应用不用，这部分内存也无法给其他进程使用。
* 适用场景： Oracle、MySQL、PostgreSQL 等大型数据库，以及 KVM 虚拟机。

B. 透明大页 (Transparent HugePages, THP)

* 原理： 内核的一个后台线程（khugepaged）会自动尝试将连续的小页合并成大页。
* 优点： 自动化，无需人工配置。
* 缺点： 在某些内存碎片严重的场景下，自动合并过程会导致系统短暂“卡顿”（CPU 抖动）。
* 现状： 很多高性能数据库（如 Redis, MongoDB）建议禁用 THP，改用静态大页。

## 4. 关键配置参考
你可以通过以下命令查看当前系统的大页情况：

grep Huge /proc/meminfo

常见的输出项：

* HugePages_Total: 系统总共有多少个大页。
* HugePages_Free: 还有多少大页未被分配。
* Hugepagesize: 默认大页大小（通常是 2048 kB）。

## 5. 什么时候该用它？

并不是所有应用都需要大页。它最适合以下场景：

* 大规模内存占用： 进程需要几十 GB 甚至上百 GB 内存。
* 频繁随机访问： 内存访问模式非常分散，导致 TLB 压力大。
* 虚拟化宿主机： 减少虚拟机内存翻译的损耗。

注意： 如果你的应用内存占用很小（比如几百 MB），开启大页反而可能因为内存碎片和管理开销导致性能下降。

## 6. 编程实现：在 C/C++ 代码中指定使用hugepage
方法 A：使用 mmap (推荐)

通过在 mmap 的标志位中加入 MAP_HUGETLB。
```cpp
#include <sys/mman.h>
#include <stdio.h>
#include <stdlib.h>

int main() {
    size_t size = 2 * 1024 * 1024; // 申请一个 2MB 的大页
    void *addr = mmap(NULL, size, PROT_READ | PROT_WRITE,
                      MAP_PRIVATE | MAP_ANONYMOUS | MAP_HUGETLB, -1, 0);

    if (addr == MAP_FAILED) {
        perror("mmap");
        return 1;
    }

    printf("成功申请到大页，地址: %p\n", addr);
    munmap(addr, size);
    return 0;
}
```
方法 B：使用 shmget (共享内存)

使用 SHM_HUGETLB 标志来申请大页段。

### 如何确认程序真的用上了？

程序运行后，不要只看 top，要看内核统计：
watch -n 1 'grep -i huge /proc/meminfo'

* HugePages_Total: 你预留的总数。
* HugePages_Free: 如果这个数值减小了，说明你的程序成功占用了大页内存。
* HugePages_Rsvd: 已经被预订但尚未分配的页面。

### /sys/kernel/mm/hugepages/hugepages-2048kB
你可以把 /sys/kernel/mm/hugepages/hugepages-2048kB 看作是一个专门管理 2MB 大页的“控制面板”。在这个文件夹里，通常包含以下几个关键的控制文件：

#### 1. 这个目录里有什么？
如果你进入这个目录（ls /sys/kernel/mm/hugepages/hugepages-2048kB/），你会看到：

* nr_hugepages：最重要的文件。 它显示（并允许你修改）当前系统预留的大页数量。
* free_hugepages：当前还没被使用的空闲大页数量。
* resv_hugepages：已经被程序“预订”但还没真正写入数据的大页。
* surplus_hugepages：超出预留值、动态分配的大页（盈余大页）。

#### 2. 你可以用它做什么？
如果你想即时更改大页的数量，你不需要重启，直接“写入”这个文件即可。

操作示例： 假设你想分配 1024 个 2MB 的大页（共 2GB 内存）：

echo 1024 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages

此时，内核会尝试在物理内存中寻找连续的空间。如果成功，你通过 cat nr_hugepages 就能看到数值变成了 1024。

#### C++代码 get free hugePage
```cpp
std::string getFreeHugePagesFilePath() const
{
    std::string freeHugePagesFilePath{"/sys/kernel/mm/hugepages/hugepages-2048kB/"};
    freeHugePagesFilePath += "free_hugepages";
    return freeHugePagesFilePath;
}

int64_t getFreePagesAmount() const
{
    const std::string hugePagesFilePath = getFreeHugePagesFilePath();
    const int direction = static_cast<uint32_t>(O_RDONLY) | static_cast<uint32_t>(O_NONBLOCK);
    int fd = ::common::utils::FileOperation::open(hugePagesFilePath.c_str(), direction);
    if (fd < 0)
    {
        printf("mem/sdm: failed to open %s errno=%i", hugePagesFilePath.c_str(), errno);
        return -1L;
    }

    std::array<char, sizeOfNrHugePagesStringBuffer> freeNrHugePages{};
    auto bytesRead = ::common::utils::FileOperation::read(fd, freeNrHugePages.data(), freeNrHugePages.size());
    if (bytesRead <= 0)
    {
        printf("mem/sdm: failed to read %s errno=%i", hugePagesFilePath.c_str(), errno);
        ::common::utils::FileOperation::close(fd);
        return -1L;
    }

    ::common::utils::FileOperation::close(fd);
    return strtol(freeNrHugePages.data(), nullptr, 10);
}
```
#### C++代码increase new hugepage
```cpp
std::string getNrHugePagesFilePath() const
{
    std::string nrHugePagesFilePath{"/sys/kernel/mm/hugepages/hugepages-2048kB/"};
    nrHugePagesFilePath += "nr_hugepages";
    return nrHugePagesFilePath;
}

bool readHugePageAmount(int& fd, char* initialNrHugePages) const
{
    const std::string nrHugePagesFilePath = getNrHugePagesFilePath();
    const int direction = static_cast<uint32_t>(O_RDWR) | static_cast<uint32_t>(O_NONBLOCK);
    fd = ::common::utils::FileOperation::open(nrHugePagesFilePath.c_str(), direction);
    if (fd >= 0)
    {
        auto bytesRead = ::common::utils::FileOperation::read(fd, initialNrHugePages, sizeOfNrHugePagesStringBuffer);
        if (bytesRead > 0)
        {
            printf("Initial Hugepages %ld", strtol(initialNrHugePages, nullptr, 10));
            return GLO_TRUE;
        }
        ::common::utils::FileOperation::close(fd);
    }

    printf("mem/sdm: failed to open/read %s errno=%i", nrHugePagesFilePath.c_str(), errno);
    return GLO_FALSE;
}

bool writeHugePageAmount(
    const int fd,
    const char* initialNrHugePages,
    const uint64_t nrOfNewPagesRequired) const
{
    std::array<char, sizeOfNrHugePagesStringBuffer> newNrHugePages{};
    (void)snprintf(
        newNrHugePages.data(),
        newNrHugePages.size(),
        "%li",
        strtol(initialNrHugePages, nullptr, 10) + static_cast<int64_t>(nrOfNewPagesRequired));
    if (::common::utils::FileOperation::write(fd, newNrHugePages.data(), newNrHugePages.size()) !=
        sizeOfNrHugePagesStringBuffer)
    {
        printf("mem/sdm: failed to write: %s errno=%i", newNrHugePages.data(), errno);
        ::common::utils::FileOperation::close(fd);
        return GLO_FALSE;
    }
    return GLO_TRUE;
}
```


## 7. mmap Hugepage vs DPDK Hugepage

### 7.1. 底层机制

| 特性 | mmap Hugepage | DPDK Hugepage |
|------|---------------|---------------|
| 实现方式 | 直接使用 Linux 内核的 hugetlbfs | 基于 hugetlbfs 构建的用户态内存管理 |
| 管理层 | 内核管理 | DPDK EAL (Environment Abstraction Layer) 管理 |
| 虚拟地址 | 由内核分配 | DPDK 可控制虚拟地址布局 |

### 7.2. 使用方式

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

### 7.3. 关键区别

| 方面 | mmap Hugepage | DPDK Hugepage |
|------|---------------|---------------|
| **物理地址访问** | 无法直接获取 | 提供 `rte_mem_virt2phy()` 获取物理地址 |
| **IOVA 连续性** | 不保证 | 可配置 IOVA 连续内存用于 DMA |
| **NUMA 感知** | 需手动处理 | 内置 NUMA 感知分配 |
| **内存池** | 无 | 提供 mempool 管理 |
| **多进程共享** | 需自己管理 | 原生支持主/从进程模式 |
| **内存碎片** | 可能碎片化 | 内置内存分配器减少碎片 |

### 7.4. 适用场景

**mmap Hugepage 适合:**
- 简单的大内存分配
- 不需要 DMA 的场景
- 不依赖 DPDK 的应用

**DPDK Hugepage 适合:**
- 高性能网络 I/O（需要物理地址给网卡 DMA）
- 需要 NUMA 优化
- 多进程共享 NIC 队列
- 零拷贝数据包处理

### 7.5. 性能对比

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
