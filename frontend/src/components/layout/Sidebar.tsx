import { NavLink } from "react-router-dom";
import { useAlertsStore } from "../../store/alertsStore";
import { UserAccountPanel } from "./UserAccountPanel";
import { APP_BRAND_MARK, APP_NAME } from "../../utils/constants";

export function Sidebar() {
  const unreadCount = useAlertsStore((s) => s.unreadCount);
  const nav = [
    { label: "EUR/USD", path: "/equity/forex?symbol=EURUSD", key: "FX", hint: "Primary" },
    { label: "Charts", path: "/forex/chart?symbol=EURUSD&market=FX", key: "CH", hint: "Forex" },
    { label: "Research", path: "/equity/research?q=EURUSD", key: "RES", hint: "FX" },
    { label: "MTA", path: "/equity/mta?symbol=EURUSD&market=FX", key: "MT", hint: "Multi-TF" },
    { label: "Paper", path: "/equity/paper?symbol=EURUSD", key: "P", hint: "Paper only" },
    { label: "Position Sizer", path: "/equity/position-sizer?symbol=EURUSD", key: "PS", hint: "Risk" },
    { label: "Journal", path: "/equity/journal?symbol=EURUSD", key: "J", hint: "Trading" },
    { label: "News", path: "/equity/news?symbol=EURUSD&market=FX", key: "NW", hint: "Macro" },
    { label: "Alerts", path: "/equity/alerts", key: "A" },
    { label: "Risk", path: "/equity/risk", key: "R" },
    { label: "Settings", path: "/equity/settings", key: "F6" },
    { label: "Backtesting", path: "/backtesting?symbol=EURUSD&market=FX", key: "BT", hint: "FX" },
  ];

  return (
    <aside className="relative z-30 flex h-full w-48 shrink-0 flex-col border-r border-terminal-border bg-terminal-panel p-0">
      <div className="border-b border-terminal-border bg-terminal-panel px-3 py-2">
        <img src={APP_BRAND_MARK} alt={APP_NAME} className="h-8 w-auto object-contain" />
      </div>
      <div className="border-b border-terminal-border px-3 py-2 text-[11px] text-terminal-muted">
        BENSIM TRADING
      </div>
      <div className="space-y-1 border-b border-terminal-border p-2 text-xs">
        <NavLink to="/equity/forex?symbol=EURUSD" className="block rounded px-2 py-2 text-terminal-muted hover:bg-terminal-bg hover:text-terminal-text">
          Home
        </NavLink>
        <NavLink to="/equity/forex?symbol=EURUSD" className="block rounded px-2 py-2 text-terminal-accent hover:bg-terminal-bg hover:text-terminal-text">
          EUR/USD Focus
        </NavLink>
      </div>
      <nav className="flex-1 space-y-1 overflow-auto p-2 text-xs">
        {nav.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            className={({ isActive }) =>
              `flex cursor-pointer items-center justify-between rounded px-2 py-2 ${
                isActive
                  ? "bg-terminal-accent/20 text-terminal-accent"
                  : "text-terminal-muted hover:bg-terminal-bg hover:text-terminal-text"
              }`
            }
          >
            <div className="flex flex-col">
              <span>{item.label}</span>
              {(item as any).hint && <span className="text-[8px] text-terminal-accent/70 -mt-0.5 uppercase">{(item as any).hint}</span>}
            </div>
            <span className="text-[10px]">
              {item.path === "/equity/alerts" && unreadCount > 0 ? `${unreadCount}` : item.key}
            </span>
          </NavLink>
        ))}
      </nav>
      <UserAccountPanel />
    </aside>
  );
}
