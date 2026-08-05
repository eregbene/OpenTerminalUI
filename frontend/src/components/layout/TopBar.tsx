import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import type { SearchSymbolItem } from "../../api/client";
import { NotificationBell } from "../notifications/NotificationBell";
import { useNavigationHistory } from "../../hooks/useNavigationHistory";
import { useRecentSecurities } from "../../hooks/useRecentSecurities";
import { useStockStore } from "../../store/stockStore";
import { APP_BRAND_MARK, APP_NAME } from "../../utils/constants";
import { normalizeForexPair } from "../../utils/instruments";

type TopBarProps = {
  hideTickerLoader?: boolean;
  hideMarketMarquee?: boolean;
};

const BRAND_ICON_SRC = APP_BRAND_MARK;
const DEFAULT_PAIR = "EURUSD";
const DEFAULT_PAIR_ROUTE = "/equity/forex?symbol=EURUSD";
const DEFAULT_CHART_ROUTE = "/forex/chart?symbol=EURUSD&market=FX";

export function TopBar({ hideTickerLoader = false, hideMarketMarquee: _hideMarketMarquee = false }: TopBarProps) {
  const navigate = useNavigate();
  const setTicker = useStockStore((s) => s.setTicker);
  const stock = useStockStore((s) => s.stock);
  const ticker = useStockStore((s) => s.ticker);
  const { addRecent } = useRecentSecurities();
  const { breadcrumbs } = useNavigationHistory({ autoTrack: true });

  const [query, setQuery] = useState(ticker || DEFAULT_PAIR);
  const [results, setResults] = useState<SearchSymbolItem[]>([]);
  const [isSuggestionsOpen, setIsSuggestionsOpen] = useState(false);
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const searchRequestRef = useRef(0);
  const suppressSuggestionsRef = useRef(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const collapsedBreadcrumbs = useMemo(() => {
    if (breadcrumbs.length <= 5) return breadcrumbs;
    return [
      breadcrumbs[0],
      breadcrumbs[1],
      { label: "...", path: breadcrumbs[breadcrumbs.length - 2]?.path || breadcrumbs[0].path },
      ...breadcrumbs.slice(-2),
    ];
  }, [breadcrumbs]);

  useEffect(() => {
    setQuery(ticker || DEFAULT_PAIR);
  }, [ticker]);

  useEffect(() => {
    const normalizedTicker = (ticker || DEFAULT_PAIR).trim().toUpperCase();
    if (!normalizedTicker) return;

    const resolvedSymbol = String(stock?.ticker || stock?.symbol || "").trim().toUpperCase();
    if (resolvedSymbol && resolvedSymbol !== normalizedTicker) return;

    addRecent(
      normalizedTicker,
      stock?.company_name || `${normalizedTicker.slice(0, 3)}/${normalizedTicker.slice(3)} Forex`,
      "forex",
      "FX",
      typeof stock?.current_price === "number" ? stock.current_price : undefined,
      typeof stock?.change_pct === "number" ? stock.change_pct : undefined,
    );
  }, [
    addRecent,
    stock?.change_pct,
    stock?.company_name,
    stock?.current_price,
    stock?.symbol,
    stock?.ticker,
    ticker,
  ]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const tag = target?.tagName?.toLowerCase();
      const editing =
        tag === "input" || tag === "textarea" || tag === "select" || Boolean(target?.isContentEditable);

      if (event.key === "/" && !editing) {
        event.preventDefault();
        searchInputRef.current?.focus();
        searchInputRef.current?.select();
        return;
      }
      if ((event.key === "m" || event.key === "M") && !editing) {
        event.preventDefault();
        navigate(DEFAULT_PAIR_ROUTE);
        return;
      }
      if (event.key === "Escape") {
        if (results.length > 0) {
          setResults([]);
          setIsSuggestionsOpen(false);
          return;
        }
        if (editing && tag === "input") {
          (target as HTMLInputElement).blur();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate, results.length]);

  const doSearch = useCallback(async (q: string) => {
    if (suppressSuggestionsRef.current || q.length < 2) {
      setResults([]);
      setIsSuggestionsOpen(false);
      return;
    }

    const requestId = ++searchRequestRef.current;
    const fxPair = normalizeForexPair(q) || (q.trim().length >= 2 ? DEFAULT_PAIR : null);
    const nextResults: SearchSymbolItem[] = fxPair
      ? [{ ticker: fxPair, name: `${fxPair.slice(0, 3)}/${fxPair.slice(3)} Forex`, exchange: "FX", country_code: "FX" }]
      : [];

    if (requestId !== searchRequestRef.current || suppressSuggestionsRef.current) return;
    setResults(nextResults);
    setIsSuggestionsOpen(nextResults.length > 0);
  }, []);

  const selectTicker = useCallback((value: string | SearchSymbolItem) => {
    const item = typeof value === "string" ? null : value;
    const inputSymbol = (typeof value === "string" ? value : value.ticker).trim().toUpperCase();
    const symbol = normalizeForexPair(inputSymbol) ?? DEFAULT_PAIR;

    suppressSuggestionsRef.current = true;
    searchRequestRef.current += 1;
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
    setResults([]);
    setIsSuggestionsOpen(false);
    setQuery(symbol);
    setTicker(symbol);
    addRecent(symbol, item?.name || `${symbol.slice(0, 3)}/${symbol.slice(3)} Forex`, "forex", "FX");
    navigate(`/equity/forex?symbol=${encodeURIComponent(symbol)}`);
  }, [addRecent, navigate, setTicker]);

  return (
    <div className="relative z-20 border-b border-terminal-border bg-terminal-panel">
      <div className="relative flex items-center gap-2 overflow-x-auto px-3 py-1.5">
        <Link
          to={DEFAULT_PAIR_ROUTE}
          className="inline-flex h-7 items-center rounded border border-terminal-border bg-terminal-bg px-1.5"
          aria-label={`${APP_NAME} Home`}
        >
          <img src={BRAND_ICON_SRC} alt={APP_NAME} className="h-5 w-5 object-contain" />
        </Link>
        <div className="flex shrink-0 items-center gap-2">
          <Link className="rounded border border-terminal-border px-2 py-1 text-[11px] text-terminal-muted hover:text-terminal-text" to={DEFAULT_PAIR_ROUTE}>
            HOME
          </Link>
          <Link className="rounded border border-terminal-border px-2 py-1 text-[11px] text-terminal-accent hover:text-terminal-text" to={DEFAULT_PAIR_ROUTE}>
            EUR/USD
          </Link>
          <Link className="rounded border border-terminal-border px-2 py-1 text-[11px] text-terminal-muted hover:text-terminal-text" to={DEFAULT_CHART_ROUTE}>
            CHARTS
          </Link>
          <Link className="rounded border border-terminal-border px-2 py-1 text-[11px] text-terminal-muted hover:text-terminal-text" to="/equity/paper?symbol=EURUSD">
            PAPER
          </Link>
          <Link className="rounded border border-terminal-border px-2 py-1 text-[11px] text-terminal-muted hover:text-terminal-text" to="/equity/journal?symbol=EURUSD">
            JOURNAL
          </Link>
        </div>
        {!hideTickerLoader ? (
          <div className="ml-2 flex min-w-0 flex-[1.4] items-center gap-1 md:min-w-[360px] xl:min-w-[460px]">
            <input
              ref={searchInputRef}
              className="w-full rounded border border-terminal-border bg-terminal-bg px-2 py-1 text-xs outline-none focus:border-terminal-accent"
              placeholder="Search FX pair: EURUSD"
              value={query}
              onChange={(e) => {
                const next = e.target.value.toUpperCase();
                suppressSuggestionsRef.current = false;
                setQuery(next);
                setIsSuggestionsOpen(next.length >= 2);
                if (debounceRef.current) clearTimeout(debounceRef.current);
                debounceRef.current = setTimeout(() => {
                  void doSearch(next);
                }, 300);
              }}
              onFocus={() => {
                if (results.length > 0 && query.length >= 2) {
                  setIsSuggestionsOpen(true);
                }
              }}
              onBlur={() => {
                setTimeout(() => setIsSuggestionsOpen(false), 120);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") selectTicker(query);
                if (e.key === "Escape") {
                  setResults([]);
                  setIsSuggestionsOpen(false);
                }
              }}
            />
            <button
              className="rounded bg-terminal-accent px-2 py-1 text-xs font-medium text-black"
              onClick={() => selectTicker(query)}
            >
              Load
            </button>
          </div>
        ) : null}
        <div className="inline-flex shrink-0 items-center gap-1 border-l border-terminal-border pl-2 text-[11px] uppercase tracking-wide text-terminal-muted">
          <span className="rounded border border-terminal-border px-2 py-1 text-terminal-accent">FX</span>
          <span>EUR/USD focus</span>
        </div>
        <NotificationBell />
        <Link
          to={DEFAULT_PAIR_ROUTE}
          className="inline-flex h-7 shrink-0 items-center border-l border-terminal-border pl-2"
          aria-label={`${APP_NAME} Home (Top Right)`}
          title={APP_NAME}
        >
          <img src={BRAND_ICON_SRC} alt={APP_NAME} className="h-5 w-5 object-contain" />
        </Link>
        {!hideTickerLoader && isSuggestionsOpen && results.length > 0 && (
          <div className="absolute left-3 right-3 top-10 z-10 max-h-72 overflow-auto rounded border border-terminal-border bg-terminal-panel">
            {results.map((item) => (
              <button
                key={`${item.ticker}:${item.name}`}
                className="block w-full border-b border-terminal-border px-3 py-2 text-left text-sm hover:bg-terminal-bg"
                onMouseDown={(event) => {
                  event.preventDefault();
                  selectTicker(item);
                }}
                onClick={() => selectTicker(item)}
              >
                <span className="inline-flex items-center gap-2">
                  <span className="rounded border border-terminal-border px-1 text-[10px] text-terminal-accent">FX</span>
                  <span>{item.ticker}</span>
                  <span className="text-terminal-muted">- {item.name}</span>
                  <span className="text-terminal-muted">(FX)</span>
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="border-t border-terminal-border/60 px-3 py-1">
        <div className="flex flex-wrap items-center gap-1 text-[10px] uppercase tracking-[0.12em]">
          {collapsedBreadcrumbs.map((crumb, index) => {
            const isCurrent = index === collapsedBreadcrumbs.length - 1;
            const isEllipsis = crumb.label === "...";
            return (
              <span key={`${crumb.path}:${index}`} className="inline-flex items-center gap-1">
                {isEllipsis ? (
                  <span className="text-terminal-muted/80">{crumb.label}</span>
                ) : isCurrent ? (
                  <span className="text-terminal-text">{crumb.label}</span>
                ) : (
                  <button
                    type="button"
                    className="text-terminal-muted hover:text-terminal-text"
                    onClick={() => navigate(crumb.path)}
                  >
                    {crumb.label}
                  </button>
                )}
                {!isCurrent ? <span className="text-terminal-muted/60">&gt;</span> : null}
              </span>
            );
          })}
        </div>
      </div>
    </div>
  );
}
