import { api } from "./base";

export type PropFirmProfile = {
  profile_id: string;
  provider_name: string;
  program_name: string;
  phase: string;
  account_size: number;
  profit_target_percent: number;
  maximum_daily_loss_percent: number;
  maximum_total_loss_percent: number;
  automated_trading_allowed: string;
};

export type PropFirmSimulation = {
  job_id: string;
  status: string;
  read_only: boolean;
  report: Record<string, unknown>;
  provider_calls: number;
  ibkr_calls: number;
};

export const fetchPropFirmProfiles = async () => (await api.get<{ items: PropFirmProfile[] }>("/research/prop-firms/profiles")).data.items;
export const runPropFirmSimulation = async (payload: Record<string, unknown>) => (await api.post<PropFirmSimulation>("/research/prop-firms/simulations", payload)).data;
export const fetchPropFirmSimulations = async () => (await api.get<{ items: PropFirmSimulation[] }>("/research/prop-firms/simulations")).data.items;
export const fetchPropFirmLeaderboard = async () => (await api.get<{ items: Array<Record<string, unknown>> }>("/research/prop-firms/leaderboard")).data.items;
