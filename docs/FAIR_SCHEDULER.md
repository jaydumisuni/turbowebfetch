# Fair Browser Task Scheduler

TurboWebFetch uses two independent capacity boundaries:

1. the fair task scheduler controls admission across requests and domains;
2. the existing Python process semaphore remains an internal final guard on actual process spawning.

The token-bucket rate limiter is separate. It controls request frequency, not concurrent browser capacity.

## Guarantees

- a configurable global task limit;
- a configurable per-domain task limit;
- FIFO order for requests from the same domain;
- cross-domain fairness so one domain cannot monopolise the queue;
- bounded queue wait time;
- queued-request cancellation through the scheduler API;
- automatic, idempotent lease release after success or failure;
- dynamic batch submission without fixed chunk barriers;
- duplicate URL coalescing while preserving input result order;
- worker rejection converted into a structured batch error.

## Configuration

| Environment variable | Default | Meaning |
| --- | ---: | --- |
| `TURBOFETCH_MAX_PROCESSES` | `14` | Global scheduled task and Python process ceiling. |
| `TURBOFETCH_MAX_PROCESSES_PER_DOMAIN` | `2` | Maximum scheduled tasks active for one hostname. Values above the global ceiling are clamped. |
| `TURBOFETCH_QUEUE_TIMEOUT` | `30000` | Maximum milliseconds a task may wait for scheduler admission. `0` disables queue expiry. |

## Failure behaviour

A task that times out or is cancelled while queued never consumes capacity. A task that throws after admission releases its capacity in a `finally` boundary. Calling a lease's `release()` more than once has no effect.

Scheduler rejection is returned by the public fetch boundary as `POOL_EXHAUSTED`. Failures from the underlying Python/Chrome worker keep their existing error classification.

## Proof

The scheduler test suite covers:

- global and per-domain limits;
- cross-domain fairness;
- queue timeout;
- queued cancellation;
- idempotent release;
- release after a thrown worker;
- closure behaviour;
- a sixty-task, five-domain stress run;
- dynamic batch ordering and duplicate handling;
- unexpected batch worker failure.
