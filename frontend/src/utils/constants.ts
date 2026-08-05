export const APP_NAME = "Bensim Trading";
export const APP_SHORT_NAME = "Bensim";
export const APP_LEGAL_NAME = "Bensim Trading";
export const APP_VERSION = "0.6.0";
export const APP_TAGLINE = "Analyze. Trade. Optimize.";
export const APP_DESCRIPTION = "Professional multi-asset trading workstation for research, analytics, portfolio, risk, and execution workflows.";
export const APP_BRAND_MARK = "/bensim-mark.svg";
export const APP_REPO_URL = "https://github.com/Hitheshkaranth/OpenTerminalUI";

export const MOMENTUM_ROTATION_BASKET = [
  "RELIANCE",
  "TCS",
  "INFY",
  "HDFCBANK",
  "ICICIBANK",
  "ITC",
  "HINDUNILVR",
  "SBIN",
  "BHARTIARTL",
  "LT",
];

export const MOMENTUM_ROTATION_BASKET_CSV = MOMENTUM_ROTATION_BASKET.join(",");

export const TIMEFRAMES = [
  { interval: "1m", range: "5d", label: "1m" },
  { interval: "5m", range: "1mo", label: "5m" },
  { interval: "15m", range: "1mo", label: "15m" },
  { interval: "1h", range: "3mo", label: "1h" },
  { interval: "1d", range: "1y", label: "1D" },
  { interval: "1wk", range: "5y", label: "1W" },
  { interval: "1mo", range: "max", label: "1M" }
];
