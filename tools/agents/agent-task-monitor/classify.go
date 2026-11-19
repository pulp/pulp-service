package main

import (
	"slices"
	"sort"
	"time"
)

// schedulerLagGrace is how long a waiting task may sit before the dispatcher
// is expected to have picked it up.
const schedulerLagGrace = 30 * time.Second

// UnexplainedWait is a waiting task with no explanation for its wait.
type UnexplainedWait struct {
	TaskHref           string
	TaskName           string
	RequestedResources []string
}

// WaitClassification is the result of classifying the waiting tasks of a poll.
type WaitClassification struct {
	CapacityBlocked int
	ResourceBlocked int
	QueueBlocked    int
	UnexplainedWait int
	// ResourceQueues has one entry per contended primary resource (raw PRN),
	// sorted by waiting count, descending.
	ResourceQueues []ResourceQueue
	// Unexplained lists unexplained-wait tasks in encounter order.
	Unexplained []UnexplainedWait
}

// tasksConflict reports whether waiter w conflicts with an earlier task e.
func tasksConflict(w, e Task) bool {
	// An exclusive request conflicts with any request for the same resource.
	for _, r := range w.Exclusive {
		if slices.Contains(e.Exclusive, r) || slices.Contains(e.Shared, r) {
			return true
		}
	}
	// A shared request only conflicts with an exclusive request.
	for _, r := range w.Shared {
		if slices.Contains(e.Exclusive, r) {
			return true
		}
	}
	return false
}

// isEarlier reports whether a is scheduled strictly before b (ties broken by href).
func isEarlier(a, b Task) bool {
	if !a.Created.Equal(b.Created) {
		return a.Created.Before(b.Created)
	}
	return a.Href < b.Href
}

// classifyWaitTasks classifies each waiting task by why it is waiting, in order:
//
//  1. no resources requested: capacity blocked
//  2. a running task holds a conflicting lock: resource blocked
//  3. an earlier-created waiting task contends for one of its resources: queue blocked
//  4. every worker is busy: capacity blocked (worker saturation)
//  5. created less than 30s before the window end: capacity blocked (scheduler lag)
//  6. otherwise: unexplained wait (orphaned locks, applock bugs, FIFO starvation)
//
// The head-of-line task for a contended resource is not blocked by its
// followers (they hold nothing), so when workers are free and it has waited
// long enough it is an unexplained wait and its resource queue is an anomaly.
func classifyWaitTasks(running, waiting []Task, onlineWorkers, totalRunning int, windowEnd time.Time) WaitClassification {
	runningExclusive := map[string]bool{}
	runningShared := map[string]bool{}
	for _, rt := range running {
		for _, r := range rt.Exclusive {
			runningExclusive[r] = true
		}
		for _, r := range rt.Shared {
			runningShared[r] = true
		}
	}

	var result WaitClassification
	type queue struct {
		waiting int
		anomaly bool
		order   int
	}
	queues := map[string]*queue{}
	bump := func(resource string, anomaly bool) {
		q, ok := queues[resource]
		if !ok {
			q = &queue{order: len(queues)}
			queues[resource] = q
		}
		q.waiting++
		if anomaly {
			q.anomaly = true
		}
	}

	for i, w := range waiting {
		requested := append(append([]string{}, w.Exclusive...), w.Shared...)

		// 1. The task requests no resources
		if len(requested) == 0 {
			result.CapacityBlocked++
			continue
		}
		primary := requested[0]

		// 2. Blocked by a running task
		blockedByRunning := false
		for _, r := range w.Exclusive {
			if runningExclusive[r] || runningShared[r] {
				blockedByRunning = true
				break
			}
		}
		if !blockedByRunning {
			for _, r := range w.Shared {
				if runningExclusive[r] {
					blockedByRunning = true
					break
				}
			}
		}
		if blockedByRunning {
			result.ResourceBlocked++
			bump(primary, false)
			continue
		}

		// 3. Blocked in the queue: an earlier waiting task conflicts with it
		// Only an earlier-created waiting task can be ahead of us in line.
		blockedByQueue := false
		for j, other := range waiting {
			if j != i && isEarlier(other, w) && tasksConflict(w, other) {
				blockedByQueue = true
				break
			}
		}
		if blockedByQueue {
			result.QueueBlocked++
			bump(primary, false)
			continue
		}

		// 4. Workers are saturated
		if onlineWorkers > 0 && totalRunning >= onlineWorkers {
			result.CapacityBlocked++
			continue
		}

		// 5. Created less than 30s before windowEnd (schedulerLagGrace),
		//    so the dispatcher may not have picked it up yet
		if windowEnd.Sub(w.Created) < schedulerLagGrace {
			result.CapacityBlocked++
			continue
		}

		// 6. None of the above
		result.UnexplainedWait++
		bump(primary, true)
		result.Unexplained = append(result.Unexplained, UnexplainedWait{
			TaskHref:           w.Href,
			TaskName:           w.Name,
			RequestedResources: requested,
		})
	}

	type entry struct {
		ResourceQueue
		order int
	}
	entries := make([]entry, 0, len(queues))
	for resource, q := range queues {
		status := "normal"
		if q.anomaly {
			status = "anomaly"
		}
		entries = append(entries, entry{ResourceQueue{Resource: resource, Waiting: q.waiting, Status: status}, q.order})
	}
	// Stable on first-seen order for equal counts
	sort.Slice(entries, func(a, b int) bool {
		if entries[a].Waiting != entries[b].Waiting {
			return entries[a].Waiting > entries[b].Waiting
		}
		return entries[a].order < entries[b].order
	})
	for _, e := range entries {
		result.ResourceQueues = append(result.ResourceQueues, e.ResourceQueue)
	}
	return result
}
