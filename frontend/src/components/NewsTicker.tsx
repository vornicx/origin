import { useNewsCache } from "../hooks/useNewsCache";

export function NewsTicker() {
  const articles = useNewsCache();

  const headlines = articles.length
    ? articles.map((a) => `${a.source}: ${a.title}`)
    : ["Origin SYSTEM ONLINE", "ALL SUBSYSTEMS NOMINAL", "AWAITING COMMANDS"];

  const text = headlines.join("  ·  ");

  return (
    <div className="news-ticker">
      <div className="news-ticker__track">
        <span className="news-ticker__text">{text}</span>
        <span className="news-ticker__text" aria-hidden="true">{text}</span>
      </div>
    </div>
  );
}
