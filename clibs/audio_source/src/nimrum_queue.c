// nimrum_queue.c — Thread-safe FIFO queue (Linux/pthread only)

#include "nimrum_queue.h"
#include "nimrum_log.h"

#define _Q_LOCK(Q)   do { if (!(Q)->noMutex) pthread_mutex_lock(&(Q)->lock); } while (0)
#define _Q_UNLOCK(Q) do { if (!(Q)->noMutex) pthread_mutex_unlock(&(Q)->lock); } while (0)

static unsigned char *data_addr(nimrum_queue_t *Q, int idx) {
    return Q->data + (Q->item_length * idx);
}

int nimrum_q_init(nimrum_queue_t *Q, int size, int item_length,
                  unsigned char *data) {
    Q->readIdx = 0;
    Q->writeIdx = 0;
    Q->fifoCnt = 0;
    Q->silent = 0;
    Q->fifo_length = size;
    Q->item_length = item_length;
    Q->data = data;
    Q->noMutex = 0;
    pthread_mutex_init(&Q->lock, NULL);
    return 0;
}

int nimrum_q_close(nimrum_queue_t *Q) {
    pthread_mutex_destroy(&Q->lock);
    return 0;
}

int nimrum_q_get_count(nimrum_queue_t *Q) {
    _Q_LOCK(Q);
    int res = Q->fifoCnt;
    _Q_UNLOCK(Q);
    return res;
}

int nimrum_q_push_many(nimrum_queue_t *Q, unsigned char *data,
                       unsigned int count) {
    _Q_LOCK(Q);
    if (((int)count + Q->fifoCnt) > Q->fifo_length) {
        if (!Q->silent) {
            nimrum_err("nimrum_q_push_many: no space. cnt:%d len:%d\n",
                       Q->fifoCnt, Q->fifo_length);
        }
        _Q_UNLOCK(Q);
        return -1;
    }
    _Q_UNLOCK(Q);

    int left = count;
    int written_bytes = 0;
    while (left > 0) {
        int until_end = Q->fifo_length - Q->writeIdx;
        int to_write = (left > until_end) ? until_end : left;
        left -= to_write;
        size_t bytes = to_write * Q->item_length;
        memcpy(data_addr(Q, Q->writeIdx), &data[written_bytes], bytes);
        written_bytes += bytes;
        Q->writeIdx += to_write;
        if (Q->writeIdx == Q->fifo_length) {
            Q->writeIdx = 0;
        }
        Q->fifoCnt += to_write;
    }
    return count;
}

int nimrum_q_peek_top_many(nimrum_queue_t *Q, unsigned char *data,
                           unsigned int count) {
    _Q_LOCK(Q);
    if (Q->fifoCnt < (int)count) {
        if (!Q->silent) {
            nimrum_err("nimrum_q_peek_top_many: cnt:%d req:%u\n",
                       Q->fifoCnt, count);
        }
        _Q_UNLOCK(Q);
        return 0;
    }

    int temp_read = Q->readIdx;
    int left = count;
    int read_bytes = 0;
    while (left > 0) {
        int until_end = Q->fifo_length - temp_read;
        int to_read = (left > until_end) ? until_end : left;
        left -= to_read;
        size_t bytes = to_read * Q->item_length;
        memcpy(&data[read_bytes], data_addr(Q, temp_read), bytes);
        read_bytes += bytes;
        temp_read += to_read;
        if (temp_read == Q->fifo_length) {
            temp_read = 0;
        }
    }
    _Q_UNLOCK(Q);
    return count;
}

int nimrum_q_remove_top_many(nimrum_queue_t *Q, unsigned int count) {
    _Q_LOCK(Q);
    if (Q->fifoCnt < (int)count) {
        if (!Q->silent) {
            nimrum_err("nimrum_q_remove_top_many: cnt:%d req:%u\n",
                       Q->fifoCnt, count);
        }
        _Q_UNLOCK(Q);
        return -1;
    }
    Q->readIdx += count;
    if (Q->readIdx >= Q->fifo_length) {
        Q->readIdx -= Q->fifo_length;
    }
    Q->fifoCnt -= count;
    _Q_UNLOCK(Q);
    return count;
}
