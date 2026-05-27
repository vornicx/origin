import { useEffect, useRef, useState } from "react";

interface WebcamBackgroundProps {
  videoRef?: React.RefObject<HTMLVideoElement>;
  onStreamChange?: (stream: MediaStream | null) => void;
}

export function WebcamBackground({ videoRef, onStreamChange }: WebcamBackgroundProps = {}) {
  const internalVideoRef = useRef<HTMLVideoElement>(null);
  const resolvedVideoRef = videoRef ?? internalVideoRef;
  const [active, setActive] = useState(false);

  useEffect(() => {
    let stream: MediaStream | null = null;
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: "user" },
          audio: false,
        });
        if (resolvedVideoRef.current) {
          resolvedVideoRef.current.srcObject = stream;
          setActive(true);
          onStreamChange?.(stream);
        }
      } catch {
        setActive(false);
        onStreamChange?.(null);
      }
    })();
    return () => {
      stream?.getTracks().forEach((t) => t.stop());
      onStreamChange?.(null);
    };
  }, [onStreamChange, resolvedVideoRef]);

  return (
    <div className={`webcam-bg ${active ? "webcam-bg--active" : ""}`}>
      <video ref={resolvedVideoRef} autoPlay playsInline muted className="webcam-bg__video" />
      <div className="webcam-bg__overlay" />
      <div className="webcam-bg__grid" />
    </div>
  );
}
