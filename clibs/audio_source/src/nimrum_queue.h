// nimrum_queue.h — Thread-safe FIFO queue (Linux/pthread only)

#pragma once

#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    unsigned char *data;
    int fifo_length;
    int item_length;
    int readIdx;
    int writeIdx;
    pthread_mutex_t lock;
    int fifoCnt;
    int silent;
    int noMutex;
} nimrum_queue_t;

int nimrum_q_init(nimrum_queue_t *Q, int size, int item_length,
                  unsigned char *data);
int nimrum_q_close(nimrum_queue_t *Q);
int nimrum_q_get_count(nimrum_queue_t *Q);
int nimrum_q_push_many(nimrum_queue_t *Q, unsigned char *data,
                       unsigned int count);
int nimrum_q_peek_top_many(nimrum_queue_t *Q, unsigned char *data,
                           unsigned int count);
int nimrum_q_remove_top_many(nimrum_queue_t *Q, unsigned int count);
