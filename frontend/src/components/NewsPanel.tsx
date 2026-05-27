import { useState, useEffect } from "react";
import { useNewsCache } from "../hooks/useNewsCache";

export function NewsPanel() {
  const news = useNewsCache();
  const [activeIdx, setActiveIdx] = useState(0);

  useEffect(() => {
    if (news.length <= 1) return;
    const id = setInterval(() => setActiveIdx((i) => (i + 1) % news.length), 8000);
    return () => clearInterval(id);
  }, [news.length]);

  const current = news[activeIdx];

  return (
    <div className="news-panel">
      <div className="news-panel__header">
        <span className="news-panel__dot" />
        <span className="news-panel__label">LIVE FEED</span>
        {news.length > 1 && (
          <span className="news-panel__count">{activeIdx + 1}/{news.length}</span>
        )}
      </div>
      {current ? (
        <div className="news-panel__card">
          {current.thumbnail ? (
            <img src={current.thumbnail} alt="" className="news-panel__thumb" />
          ) : (
            <div className="news-panel__thumb-placeholder" />
          )}
          <div className="news-panel__bar">
            <span className="news-panel__source-badge">{current.source}</span>
            <span className="news-panel__title">{current.title}</span>
          </div>
        </div>
      ) : (
        <div className="news-panel__empty">No data</div>
      )}
    </div>
  );
}
