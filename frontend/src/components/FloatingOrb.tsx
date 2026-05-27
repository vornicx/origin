import { useRef, useState, useCallback, useEffect } from "react";
import { OriginCore } from "./OriginCore";

interface FloatingOrbProps {
  size?: number;
}

export function FloatingOrb({ size = 80 }: FloatingOrbProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({
    x: Math.round(window.innerWidth * 0.62),
    y: Math.round(window.innerHeight * 0.35),
  });
  const dragging = useRef(false);
  const offset = useRef({ x: 0, y: 0 });

  const onDown = useCallback((e: React.PointerEvent) => {
    dragging.current = true;
    offset.current = { x: e.clientX - pos.x, y: e.clientY - pos.y };
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  }, [pos]);

  const onMove = useCallback((e: React.PointerEvent) => {
    if (!dragging.current) return;
    setPos({ x: e.clientX - offset.current.x, y: e.clientY - offset.current.y });
  }, []);

  const onUp = useCallback(() => {
    dragging.current = false;
  }, []);

  useEffect(() => {
    const onResize = () => {
      setPos((p) => ({
        x: Math.min(p.x, window.innerWidth - size),
        y: Math.min(p.y, window.innerHeight - size),
      }));
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [size]);

  return (
    <div
      ref={ref}
      className="floating-orb"
      style={{ left: pos.x, top: pos.y, width: size, height: size }}
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={onUp}
    >
      <OriginCore size={size} showLatency={false} />
    </div>
  );
}
