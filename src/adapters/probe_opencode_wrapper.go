// probe_opencode_wrapper.go —— 乾坤镜 v0.11.3 opencode 包装器探针
// 职责：通过官方 debug 日志实时监听 opencode 事件，发射到乾坤镜热轨
// 依赖：Go 标准库 only（零第三方依赖）
// 用法：go run probe_opencode_wrapper.go opencode [args...]
// 代码量：<200 行

package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	schemaVersion = "0.9.3"
	pollInterval  = 2 * time.Second
)

type MingEvent struct {
	System        string                 `json:"system"`
	Mode          string                 `json:"mode"`
	EventType     string                 `json:"event_type"`
	Payload       map[string]interface{} `json:"payload"`
	Timestamp     float64                `json:"timestamp"`
	MonotonicMs   float64                `json:"monotonic_ms"`
	Lamport       int                    `json:"lamport"`
	Pid           int                    `json:"_pid"`
	SchemaVersion string                 `json:"_schema_version"`
}

var (
	lamport   int
	lamportMu sync.Mutex
	seenFiles sync.Map
)

func nextLamport() int {
	lamportMu.Lock()
	defer lamportMu.Unlock()
	lamport++
	return lamport
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintf(os.Stderr, "Usage: ming-run <command> [args...]\n")
		os.Exit(1)
	}

	cmd := exec.Command(os.Args[1], os.Args[2:]...)
	cmd.Env = append(os.Environ(), "OPENCODE_DEV_DEBUG=true")
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr

	if err := cmd.Start(); err != nil {
		fmt.Fprintf(os.Stderr, "[MING] Failed to start %s: %v\n", os.Args[1], err)
		os.Exit(1)
	}

	wrapperPid := os.Getpid()
	childPid := cmd.Process.Pid

	hotDir := filepath.Join(os.Getenv("HOME"), ".ming", "hot")
	os.MkdirAll(hotDir, 0755)
	ts := time.Now().Format("20060102_150405")
	hotFile := filepath.Join(hotDir, fmt.Sprintf("opencode_%s_%d.jsonl", ts, childPid))

	// 注册事件
	emit(hotFile, "__register__", map[string]interface{}{
		"pid":            childPid,
		"mode":           "black",
		"schema_version": schemaVersion,
		"wrapper_pid":    wrapperPid,
		"registered_at":  time.Now().Unix(),
	})

	// 启动轮询
	stopPoll := make(chan struct{})
	go pollLoop(hotFile, stopPoll)

	// 等待子进程退出
	fmt.Fprintf(os.Stderr, "[MING] Watching opencode (pid=%d) via debug logs...\n", childPid)
	cmd.Wait()
	close(stopPoll)

	// 最终扫描 + 会话摘要
	time.Sleep(3 * time.Second) // 等待日志写入完成
	pollOnce(hotFile)
	writeSessionSummary(hotFile, childPid)

	emit(hotFile, "__detach__", map[string]interface{}{
		"pid":        childPid,
		"detached_at": time.Now().Unix(),
	})

	fmt.Fprintf(os.Stderr, "[MING] Session ended. Events written to %s\n", hotFile)
}

func pollLoop(hotFile string, stop chan struct{}) {
	ticker := time.NewTicker(pollInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ticker.C:
			pollOnce(hotFile)
		case <-stop:
			return
		}
	}
}

func pollOnce(hotFile string) {
	msgDir := filepath.Join(os.Getenv("HOME"), ".opencode", "messages")
	if _, err := os.Stat(msgDir); err != nil {
		return
	}

	filepath.Walk(msgDir, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() {
			return nil
		}
		ext := filepath.Ext(path)
		if ext != ".json" {
			return nil
		}

		if _, loaded := seenFiles.LoadOrStore(path, true); loaded {
			return nil
		}

		processFile(path, hotFile)
		return nil
	})
}

func processFile(filePath, hotFile string) {
	data, err := os.ReadFile(filePath)
	if err != nil {
		return
	}

	base := filepath.Base(filePath)
	dir := filepath.Base(filepath.Dir(filePath)) // session-prefix

	// 跳过非事件文件
	if strings.HasSuffix(base, "_request.json") {
		// request.json 用于 agent_step_start 计数
		emitAgentStep(hotFile, dir, base)
		return
	}
	if !strings.HasSuffix(base, "_response.json") && !strings.HasSuffix(base, "_tool_results.json") {
		return
	}

	var payload map[string]interface{}
	if err := json.Unmarshal(data, &payload); err != nil {
		return
	}

	if strings.HasSuffix(base, "_response.json") {
		emitLLMInvoke(hotFile, dir, base, payload)
		// LLM 响应后，发射 agent_step_finish
		emitAgentStepFinish(hotFile, dir, base)
	} else if strings.HasSuffix(base, "_tool_results.json") {
		emitToolCall(hotFile, dir, base, payload)
		// 工具调用完成后，发射 agent_step_finish
		emitAgentStepFinish(hotFile, dir, base)
	}
}

func emitLLMInvoke(hotFile, sessionPrefix, filename string, payload map[string]interface{}) {
	model := ""
	if m, ok := payload["model"].(string); ok {
		model = m
	}

	var inputTokens, outputTokens int
	if usage, ok := payload["usage"].(map[string]interface{}); ok {
		if v, ok := usage["prompt_tokens"].(float64); ok {
			inputTokens = int(v)
		}
		if v, ok := usage["completion_tokens"].(float64); ok {
			outputTokens = int(v)
		}
	}

	finishReason := ""
	if fr, ok := payload["finish_reason"].(string); ok {
		finishReason = fr
	}

	// 提取 provider/host
	targetHost := "unknown"
	if provider, ok := payload["provider"].(map[string]interface{}); ok {
		if h, ok := provider["host"].(string); ok {
			targetHost = h
		}
	}

	seq := extractSeq(filename)
	emit(hotFile, "llm_invoke", map[string]interface{}{
		"layer_agent": map[string]interface{}{
			"step_id":       fmt.Sprintf("seq_%d", seq),
			"session_id":    sessionPrefix,
			"agent_name":    "opencode",
			"request_seq":   seq,
		},
		"layer_llm": map[string]interface{}{
			"model":         model,
			"input_tokens":  inputTokens,
			"output_tokens": outputTokens,
			"finish_reason": finishReason,
		},
		"layer_network": map[string]interface{}{
			"target_host": targetHost,
		},
	})

	// 新增：发射 llm_output 事件（用于重复检测）
	// 从 payload 中提取输出文本（如果有）
	if content, ok := payload["content"].(string); ok && content != "" {
		emit(hotFile, "llm_output", map[string]interface{}{
			"layer_agent": map[string]interface{}{
				"step_id":    fmt.Sprintf("seq_%d", seq),
				"session_id": sessionPrefix,
				"agent_name": "opencode",
			},
			"layer_llm": map[string]interface{}{
				"output_text":      truncate(content, getContentMaxLen()),
				"output_text_hash": _computeHash(content),
			},
			"layer_network": map[string]interface{}{
				"target_host": targetHost,
			},
		})
	}
}

func emitToolCall(hotFile, sessionPrefix, filename string, payload map[string]interface{}) {
	seq := extractSeq(filename)

	// 解析 tool_results
	var toolName string
	var toolResult string
	var isError bool

	if results, ok := payload["tool_results"].([]interface{}); ok && len(results) > 0 {
		if tool, ok := results[0].(map[string]interface{}); ok {
			if name, ok := tool["tool_name"].(string); ok {
				toolName = name
			}
			if content, ok := tool["content"].(string); ok {
				toolResult = content
			}
			if isErr, ok := tool["is_error"].(bool); ok {
				isError = isErr
			}
		}
	}

	if toolName == "" {
		toolName = "unknown"
	}

	status := "success"
	if isError {
		status = "fail"
	}

	emit(hotFile, "tool_call", map[string]interface{}{
		"layer_agent": map[string]interface{}{
			"step_id":    fmt.Sprintf("seq_%d", seq),
			"session_id": sessionPrefix,
			"agent_name": "opencode",
		},
		"layer_tool": map[string]interface{}{
			"tool_name":   toolName,
			"tool_result": truncate(toolResult, getContentMaxLen()),
			"tool_status": status,
		},
		"layer_network": map[string]interface{}{
			"target_host": "local",
		},
	})
}

func emitAgentStep(hotFile, sessionPrefix, filename string) {
	seq := extractSeq(filename)
	// 发射 agent_step_start 事件（步骤开始）
	emit(hotFile, "agent_step_start", map[string]interface{}{
		"layer_agent": map[string]interface{}{
			"step_id":       fmt.Sprintf("seq_%d", seq),
			"session_id":    sessionPrefix,
			"agent_name":    "opencode",
			"step_status":   "start",
			"cycle_number":  seq,
		},
		"layer_network": map[string]interface{}{
			"target_host": "local",
		},
	})
}

func emitAgentStepFinish(hotFile, sessionPrefix, filename string) {
	seq := extractSeq(filename)
	// 发射 agent_step_finish 事件（步骤结束）
	emit(hotFile, "agent_step_finish", map[string]interface{}{
		"layer_agent": map[string]interface{}{
			"step_id":       fmt.Sprintf("seq_%d", seq),
			"session_id":    sessionPrefix,
			"agent_name":    "opencode",
			"step_status":   "finish",
			"cycle_number":  seq,
		},
		"layer_network": map[string]interface{}{
			"target_host": "local",
		},
	})
}

func writeSessionSummary(hotFile string, childPid int) {
	msgDir := filepath.Join(os.Getenv("HOME"), ".opencode", "messages")
	if _, err := os.Stat(msgDir); err != nil {
		return
	}

	var llmCount, toolCount, stepCount int
	filepath.Walk(msgDir, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() {
			return nil
		}
		base := filepath.Base(path)
		if strings.HasSuffix(base, "_response.json") {
			llmCount++
		} else if strings.HasSuffix(base, "_tool_results.json") {
			toolCount++
		} else if strings.HasSuffix(base, "_request.json") {
			stepCount++
		}
		return nil
	})

	emit(hotFile, "diagnostic_summary", map[string]interface{}{
		"layer_agent": map[string]interface{}{
			"agent_name": "opencode",
			"session_id": "summary",
		},
		"llm_invocations": llmCount,
		"tool_calls":      toolCount,
		"agent_steps":     stepCount,
	})
}

func emit(hotFile, eventType string, payload map[string]interface{}) {
	// 暂停检查：~/.ming/.paused/opencode 存在则静默
	pausedFile := filepath.Join(os.Getenv("HOME"), ".ming", ".paused", "opencode")
	if _, err := os.Stat(pausedFile); err == nil {
		return
	}

	ev := MingEvent{
		System:        "opencode",
		Mode:          "black",
		EventType:     eventType,
		Payload:       payload,
		Timestamp:     float64(time.Now().UnixMilli()) / 1000,
		MonotonicMs:   float64(time.Now().UnixMilli()),
		Lamport:       nextLamport(),
		Pid:           os.Getpid(),
		SchemaVersion: schemaVersion,
	}
	data, _ := json.Marshal(ev)
	f, err := os.OpenFile(hotFile, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		fmt.Fprintf(os.Stderr, "[MING-DROP] %v\n", err)
		return
	}
	defer f.Close()
	f.Write(data)
	f.Write([]byte{'\n'})
}

func extractSeq(filename string) int {
	parts := strings.SplitN(filename, "_", 2)
	if len(parts) > 0 {
		if seq, err := strconv.Atoi(parts[0]); err == nil {
			return seq
		}
	}
	return 0
}

func getContentMaxLen() int {
	val := os.Getenv("MING_CONTENT_MAX_LEN")
	if val == "" {
		return 2000
	}
	v, err := strconv.Atoi(val)
	if err != nil {
		return 2000
	}
	if v == 0 {
		return 0
	}
	return v
}

func truncate(s string, maxLen int) string {
	if maxLen == 0 {
		return s
	}
	if len(s) > maxLen {
		return s[:maxLen] + "..."
	}
	return s
}

func _computeHash(text string) string {
	if text == "" {
		return ""
	}
	hash := sha256.Sum256([]byte(text))
	return hex.EncodeToString(hash[:])[:16]
}
