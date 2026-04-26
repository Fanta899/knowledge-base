1.创建/打开 POSIX 共享内存对象：shm_open（要创建需带 O_CREAT）

```cpp
int oflag = writer
    ? static_cast<uint32_t>(O_RDWR) | static_cast<uint32_t>(O_CREAT) | static_cast<uint32_t>(O_TRUNC)
    : O_RDONLY;
if (controlOnly)
{
    oflag = O_RDWR;
}

fd = shm_open(sharedMemoryName, oflag, 0600);

int fd = -1;
```

2.设置对象大小（必须步骤）：ftruncate(fd, size)（否则文件可能为 0 长度）

```cpp
const size_t shMemLen = (sizeof(ShmBlock));
if ((writer and not controlOnly) && (ftruncate(fd, shMemLen) < 0))
{
    snprintf(nameOfTheErroApi, NAME_MAX, "ftruncate");
    return false;
}
```

3.把共享内存映射到进程地址空间：mmap(nullptr, size, prot, flags, fd, 0)（返回地址或 MAP_FAILED）

```cpp
int prot = PROT_READ;
if (writer or controlOnly)
{
    prot = PROT_WRITE | PROT_READ;
}
int mflags = MAP_SHARED | MAP_LOCKED;
int offset = 0;
shmBlock = reinterpret_cast<ShmBlock*>(mmap(nullptr, shMemLen, prot, mflags, fd, offset));
```

释放/清理相关 API：

* 解除映射：munmap(ptr, size)
* 关闭 fd：close(fd)
* 删除命名共享对象：shm_unlink(name)