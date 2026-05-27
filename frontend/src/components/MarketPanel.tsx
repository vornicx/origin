import { useState, useEffect, useRef } from "react";
import { apiBase } from "../config/api";

const API = apiBase();

interface MarketData {
  label: string;
  value: string;
  change?: string;
  positive?: boolean;
}

export function MarketPanel() {
  const [data, setData] = useState<MarketData[]>([]);
  const [history, setHistory] = useState<number[]>([]);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const fetch_ = async () => {
      try {
        const res = await fetch(`${API}/dashboard/market`, { signal: AbortSignal.timeout(3000) }).then((r) => r.json());
        if (res.items) setData(res.items);
        if (res.chart) setHistory(res.chart);
      } catch {
        setData([
          { label: "S&P 500", value: "5,304.72", change: "+0.41%", positive: true },
          { label: "NASDAQ", value: "16,831.48", change: "+0.28%", positive: true },
          { label: "BTC/USD", value: "104,210", change: "-1.12%", positive: false },
        ]);
        setHistory(Array.from({ length: 60 }, (_, i) => 50 + Math.sin(i * 0.15) * 20 + Math.random() * 10));
      }
    };
    fetch_();
    const id = setInterval(fetch_, 60_000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const cvs = canvasRef.current;
    if (!cvs || history.length < 2) return;
    const ctx = cvs.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const w = cvs.clientWidth;
    const h = cvs.clientHeight;
    cvs.width = w * dpr;
    cvs.height = h * dpr;
    ctx.scale(dpr, dpr);

    const min = Math.min(...history);
    const max = Math.max(...history);
    const range = max - min || 1;
    const stepX = w / (history.length - 1);

    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, "rgba(0, 212, 255, 0.12)");
    grad.addColorStop(1, "rgba(0, 212, 255, 0.0)");

    ctx.beginPath();
    ctx.moveTo(0, h);
    history.forEach((v, i) => {
      const x = i * stepX;
      const y = h - ((v - min) / range) * (h * 0.85) - h * 0.05;
      ctx.lineTo(x, y);
    });
    ctx.lineTo(w, h);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    ctx.beginPath();
    history.forEach((v, i) => {
      const x = i * stepX;
      const y = h - ((v - min) / range) * (h * 0.85) - h * 0.05;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = "#00d4ff";
    ctx.lineWidth = 1.2;
    ctx.shadowColor = "rgba(0, 212, 255, 0.5)";
    ctx.shadowBlur = 4;
    ctx.stroke();
  }, [history]);

  const now = new Date();
  const timeStr = now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  const dateStr = now.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }).toUpperCase();

  return (
    <div className="market-panel">
      <div className="market-panel__header">
        <span className="market-panel__date">{dateStr}</span>
        <span className="market-panel__time">{timeStr}</span>
      </div>
      <div className="market-panel__data">
        {data.map((d) => (
          <div key={d.label} className="market-panel__row">
            <span className="market-panel__label">{d.label}</span>
            <span className="market-panel__value">{d.value}</span>
            {d.change && (
              <span className={`market-panel__change ${d.positive ? "market-panel__change--up" : "market-panel__change--down"}`}>
                {d.change}
              </span>
            )}
          </div>
        ))}
      </div>
      <div className="market-panel__chart">
        <canvas ref={canvasRef} className="market-panel__canvas" />
      </div>
    </div>
  );
}
