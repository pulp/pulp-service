package main

import (
	"fmt"
	"reflect"
	"testing"
	"time"
)

var testWindowEnd = mustTime("2026-08-25T16:00:00Z")

func init() { retryBackoff = time.Millisecond }

func mustTime(s string) time.Time {
	t, err := time.Parse(time.RFC3339Nano, s)
	if err != nil {
		panic(err)
	}
	return t
}

func waitingTask(href, created string, exclusive ...string) Task {
	return Task{Href: href, Name: "task", State: stateWaiting, Created: mustTime(created), Exclusive: exclusive}
}

func TestClassifyQueueOnIdleWorkersIsAnomaly(t *testing.T) {
	// The head was created first and the rest pile up behind it. Nothing
	// running holds the resource and 24 of 25 workers are idle: a stuck queue.
	waiting := []Task{waitingTask("/tasks/head/", "2026-08-25T15:56:00Z", "019987f6")}
	for i := range 91 {
		waiting = append(waiting, waitingTask(fmt.Sprintf("/tasks/follower-%d/", i), "2026-08-25T15:57:00Z", "019987f6"))
	}
	running := []Task{waitingTask("/tasks/running/", "2026-08-25T15:58:00Z", "unrelated")}

	got := classifyWaitTasks(running, waiting, 25, 1, testWindowEnd)

	wantQueues := []ResourceQueue{{Resource: "019987f6", Waiting: 92, Status: "anomaly"}}
	if !reflect.DeepEqual(got.ResourceQueues, wantQueues) {
		t.Errorf("queues = %+v, want %+v", got.ResourceQueues, wantQueues)
	}
	if got.QueueBlocked != 91 || got.UnexplainedWait != 1 || got.ResourceBlocked != 0 || got.CapacityBlocked != 0 {
		t.Errorf("counts = %+v", got)
	}
	if len(got.Unexplained) != 1 || got.Unexplained[0].TaskHref != "/tasks/head/" {
		t.Errorf("unexplained = %+v", got.Unexplained)
	}
}

func TestClassifyBlockedByRunningHolderIsNormal(t *testing.T) {
	// A running task holds R1 exclusively, so the 5 waiters are legitimately blocked.
	running := []Task{waitingTask("/tasks/running/", "2026-08-25T15:58:00Z", "R1")}
	var waiting []Task
	for i := range 5 {
		waiting = append(waiting, waitingTask(fmt.Sprintf("/tasks/waiter-%d/", i), "2026-08-25T15:58:00Z", "R1"))
	}

	got := classifyWaitTasks(running, waiting, 25, 1, testWindowEnd)

	wantQueues := []ResourceQueue{{Resource: "R1", Waiting: 5, Status: "normal"}}
	if !reflect.DeepEqual(got.ResourceQueues, wantQueues) {
		t.Errorf("queues = %+v, want %+v", got.ResourceQueues, wantQueues)
	}
	if got.ResourceBlocked != 5 || got.QueueBlocked != 0 || got.UnexplainedWait != 0 || len(got.Unexplained) != 0 {
		t.Errorf("counts = %+v", got)
	}
}

func TestClassifyFreshHeadIsNotAnomaly(t *testing.T) {
	// The head arrived < 30s before the window end: too soon to call it stuck.
	waiting := []Task{
		waitingTask("/tasks/head/", "2026-08-25T15:59:45Z", "019987f6"),
		waitingTask("/tasks/follower/", "2026-08-25T15:59:50Z", "019987f6"),
	}

	got := classifyWaitTasks(nil, waiting, 25, 0, testWindowEnd)

	if got.UnexplainedWait != 0 || got.CapacityBlocked != 1 || got.QueueBlocked != 1 {
		t.Errorf("counts = %+v", got)
	}
	wantQueues := []ResourceQueue{{Resource: "019987f6", Waiting: 1, Status: "normal"}}
	if !reflect.DeepEqual(got.ResourceQueues, wantQueues) {
		t.Errorf("queues = %+v, want %+v", got.ResourceQueues, wantQueues)
	}
}

func TestClassifySharedLocks(t *testing.T) {
	created := "2026-08-25T15:50:00Z"
	shared := func(href string, res ...string) Task {
		return Task{Href: href, State: stateWaiting, Created: mustTime(created), Shared: res}
	}

	t.Run("shared waiter vs running shared holder is not blocked", func(t *testing.T) {
		running := []Task{{Href: "/r/", Shared: []string{"D"}}}
		got := classifyWaitTasks(running, []Task{shared("/w/", "D")}, 10, 1, testWindowEnd)
		if got.ResourceBlocked != 0 || got.UnexplainedWait != 1 {
			t.Errorf("counts = %+v", got)
		}
	})

	t.Run("shared waiter vs running exclusive holder is resource blocked", func(t *testing.T) {
		running := []Task{{Href: "/r/", Exclusive: []string{"D"}}}
		got := classifyWaitTasks(running, []Task{shared("/w/", "D")}, 10, 1, testWindowEnd)
		if got.ResourceBlocked != 1 {
			t.Errorf("counts = %+v", got)
		}
	})

	t.Run("exclusive waiter behind earlier shared waiter is queue blocked", func(t *testing.T) {
		waiting := []Task{
			shared("/a/", "D"),
			{Href: "/b/", State: stateWaiting, Created: mustTime("2026-08-25T15:51:00Z"), Exclusive: []string{"D"}},
		}
		got := classifyWaitTasks(nil, waiting, 10, 0, testWindowEnd)
		if got.QueueBlocked != 1 || got.UnexplainedWait != 1 {
			t.Errorf("counts = %+v", got)
		}
	})
}

func TestClassifyWorkerSaturationAndNoResources(t *testing.T) {
	waiting := []Task{
		{Href: "/none/", State: stateWaiting, Created: mustTime("2026-08-25T15:00:00Z")},
		waitingTask("/sat/", "2026-08-25T15:00:00Z", "X"),
	}
	got := classifyWaitTasks(nil, waiting, 4, 4, testWindowEnd)
	if got.CapacityBlocked != 2 || got.UnexplainedWait != 0 {
		t.Errorf("counts = %+v", got)
	}
	if len(got.ResourceQueues) != 0 {
		t.Errorf("queues = %+v, want none", got.ResourceQueues)
	}
}
