const MAJOR_CURRENCIES = new Set([
  "USD",
  "EUR",
  "GBP",
  "JPY",
  "CHF",
  "AUD",
  "CAD",
  "NZD",
  "INR",
  "CNH",
  "CNY",
  "SGD",
  "HKD",
]);

export function normalizeForexPair(value: string | null | undefined): string | null {
  const raw = String(value ?? "").trim().toUpperCase();
  const cleaned = raw.replace(/^FX:/, "").replace(/=X$/, "").replace(/[^A-Z]/g, "");
  if (cleaned.length !== 6) return null;
  const base = cleaned.slice(0, 3);
  const quote = cleaned.slice(3, 6);
  if (base === quote) return null;
  if (!MAJOR_CURRENCIES.has(base) || !MAJOR_CURRENCIES.has(quote)) return null;
  return `${base}${quote}`;
}

export function isForexPair(value: string | null | undefined): boolean {
  return normalizeForexPair(value) !== null;
}
