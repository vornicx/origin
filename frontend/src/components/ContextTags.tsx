import { ConnectionStatus } from "../types";

interface Props {
  connectionStatus: ConnectionStatus;
  messageCount: number;
  mode?: string;
}

const STATUS_LABELS: Record<ConnectionStatus, string> = {
  connected: "online",
  connecting: "connecting...",
  disconnected: "offline",
  error: "error",
};

export function ContextTags({ connectionStatus, messageCount, mode = "focus" }: Props) {
  return (
    <div className="context-tags">
      <span className={`tag tag--status tag--${connectionStatus}`}>
        <span className="tag__dot" />
        {STATUS_LABELS[connectionStatus]}
      </span>
      <span className="tag">mode: {mode}</span>
      <span className="tag">profile: vadim_vornic</span>
      {messageCount > 0 && (
        <span className="tag">{messageCount} msg</span>
      )}
    </div>
  );
}
