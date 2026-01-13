# 用C++实现FIFO循环队列

```cpp
#pragma once
#include <array>

template <typename T>
class CircularBufferFifoBase
{
public:
    explicit CircularBufferFifoBase(const size_t& newCapacity) : capacity{newCapacity} {}

    void pop()
    {
        if (front + 1 < capacity)
        {
            front++;
            queueSize--;
        }
        else if (not empty())
        {
            front = 0;
            queueSize--;
        }
    }

    template <typename Container>
    void push(T value, Container& queue)
    {
        if (queueSize >= capacity)
        {
            printf("Trying to write to full queue")
            return;
        }
        queueSize++;
        if (back < capacity)
        {
            previousBack = back;
            queue.at(back++) = value;
        }
        else
        {
            back = 0;
            previousBack = back;
            queue.at(back++) = value;
        }
    }

    [[nodiscard]] bool empty() const { return queueSize == 0; }
    [[nodiscard]] size_t size() const { return queueSize; }

    template <typename Container>
    [[nodiscard]] const T& getFront(Container& queue) const
    {
        return queue.at(front);
    }
    template <typename Container>
    [[nodiscard]] const T& getBack(Container& queue) const
    {
        return queue.at(previousBack);
    }
    template <typename Container>
    [[nodiscard]] const T& at(Container& queue, size_t index) const
    {
        return queue.at(index);
    }

protected:
    size_t front{0};
    size_t back{0};
    size_t queueSize{0};
    size_t previousBack{0};
    const size_t capacity{0};
};

template <typename T, size_t... capacity>
class CircularBufferFifo;

template <typename T, size_t capacity>
class CircularBufferFifo<T, capacity> : public CircularBufferFifoBase<T>
{
public:
    explicit CircularBufferFifo() : CircularBufferFifoBase<T>{capacity} {}

    void pop() { CircularBufferFifoBase<T>::pop(); }
    void push(T value) { CircularBufferFifoBase<T>::push(value, queue); }

    [[nodiscard]] const T& getFront() const { return CircularBufferFifoBase<T>::getFront(queue); }
    [[nodiscard]] const T& getBack() const { return CircularBufferFifoBase<T>::getBack(queue); }
    [[nodiscard]] const T& at(size_t index) const { return CircularBufferFifoBase<T>::at(queue, index); }

private:
    std::array<T, capacity> queue{};
};

template <typename T>
class CircularBufferFifo<T> : public CircularBufferFifoBase<T>
{
public:
    explicit CircularBufferFifo(const size_t& newCapacity) : CircularBufferFifoBase<T>{newCapacity}
    {
        queue.resize(newCapacity);
    }

    void pop() { CircularBufferFifoBase<T>::pop(); }
    void push(T value) { CircularBufferFifoBase<T>::push(value, queue); }

    [[nodiscard]] const T& getFront() const { return CircularBufferFifoBase<T>::getFront(queue); }
    [[nodiscard]] const T& getBack() const { return CircularBufferFifoBase<T>::getBack(queue); }

private:
    std::vector<T> queue{};
};

```