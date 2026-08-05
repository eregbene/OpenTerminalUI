import { api } from "./base";

export const fetchBrokers = async () => (await api.get<{ items: Array<Record<string, unknown>> }>("/brokers")).data.items;
export const connectBroker = async (broker = "ibkr") => (await api.post<Record<string, unknown>>(`/brokers/${broker}/connect`)).data;
export const disconnectBroker = async (broker = "ibkr") => (await api.post<Record<string, unknown>>(`/brokers/${broker}/disconnect`)).data;
export const fetchBrokerHealth = async (broker = "ibkr") => (await api.get<Record<string, unknown>>(`/brokers/${broker}/health`)).data;
export const fetchBrokerCapabilities = async (broker = "ibkr") => (await api.get<Record<string, unknown>>(`/brokers/${broker}/capabilities`)).data;
export const resolveIbkrContract = async (instrumentId: string) => (await api.post<Record<string, unknown>>("/brokers/ibkr/contracts/resolve", { instrument_id: instrumentId })).data;
export const fetchIbkrQuote = async (instrumentId: string) => (await api.get<Record<string, unknown>>(`/brokers/ibkr/quotes/${encodeURIComponent(instrumentId)}`)).data;
export const fetchIbkrHistoricalBars = async (instrumentId: string) => (await api.post<{ items: Array<Record<string, unknown>> }>("/brokers/ibkr/historical-bars", { instrument_id: instrumentId, bar_size: "15 mins", duration: "1 D" })).data.items;
export const fetchIbkrAccounts = async () => (await api.get<{ items: Array<Record<string, unknown>> }>("/brokers/ibkr/accounts")).data.items;
export const fetchIbkrSnapshot = async (accountId: string) => (await api.get<Record<string, unknown>>(`/brokers/ibkr/accounts/${accountId}/snapshot`)).data;
export const fetchIbkrPositions = async (accountId: string) => (await api.get<{ items: Array<Record<string, unknown>> }>(`/brokers/ibkr/accounts/${accountId}/positions`)).data.items;
export const fetchIbkrOrders = async (accountId: string) => (await api.get<{ items: Array<Record<string, unknown>> }>(`/brokers/ibkr/accounts/${accountId}/orders`)).data.items;
export const fetchIbkrExecutions = async (accountId: string) => (await api.get<{ items: Array<Record<string, unknown>> }>(`/brokers/ibkr/accounts/${accountId}/executions`)).data.items;
export const reconcileIbkrAccount = async (accountId: string) => (await api.post<Record<string, unknown>>(`/brokers/ibkr/accounts/${accountId}/reconcile`)).data;
