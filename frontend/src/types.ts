export interface Message {
  id: string;
  role: "user" | "assistant" | "status";
  content: string;
  timestamp: string;
  cycleId?: string;
  stage?: string;
  skillsUsed?: string[];
}

export interface ContextTag {
  label: string;
  value: string;
}

export type ConnectionStatus = "connecting" | "connected" | "disconnected" | "error";

export type WakeWordState =
  | "idle"
  | "wake_detected"
  | "listening_command"
  | "processing"
  | "speaking"
  | "stopped";

export interface WSMessage {
  type:
    | "answer"
    | "status"
    | "error"
    | "reasoning"
    | "reasoning_step"
    | "monitor_alert"
    | "wake_word_event"
    | "subconscious_thought"
    | "proactive_suggestion"
    | "workflow_run";
  content?: string;
  message?: string;
  stage?: string;
  cycle_id?: string;
  timestamp?: string;
  skills_used?: string[];
  latency_ms?: number;
  llm_latency_ms?: number;
  completion_tokens?: number;
  tokens_per_s?: number;
  provider?: string;
  // reasoning_step fields
  step?: string;
  step_number?: number;
  total_steps?: number;
  data?: {
    status?: "running" | "done";
    intent?: string;
    task_type?: string;
    confidence?: number;
    skills?: string[];
    fast_path?: boolean;
    steps_count?: number;
    success?: boolean;
    skills_executed?: string[];
    is_valid?: boolean;
    count?: number;
    length?: number;
  };
  steps?: {
    intent: string;
    plan_steps: number;
    act_success: boolean;
    check_valid: boolean;
    memories_saved: number;
  };
  alert?: {
    metric: string;
    value: number;
    threshold: number;
    title: string;
    message: string;
    severity: string;
    timestamp: string;
  };
  // wake_word_event fields
  event?: string;
  state?: WakeWordState;
  trigger?: string;
  command?: string;
  // subconscious_thought fields
  thought_type?: string;
  confidence?: number;
  // proactive_suggestion fields
  proactive_event?: {
    id: string;
    kind: string;
    source: string;
    title: string;
    message: string;
    timestamp: string;
    severity: string;
    suggested_action?: { skill?: string; inputs?: Record<string, unknown> };
    metadata?: Record<string, unknown>;
  };
  // workflow_run fields
  phase?: "started" | "finished";
  run?: {
    id: string;
    workflow_id: string;
    workflow_name: string;
    trigger_type: string;
    status: string;
    actions_executed: number;
    duration_ms: number;
    error?: string;
  };
}
