import type { StrategyEvaluation, StrategyRegistration } from "../../api/strategies";

interface Props {
  strategies: StrategyRegistration[];
  selectedStrategyId: string;
  evaluation: StrategyEvaluation | null;
  loading: boolean;
  error: string | null;
  onStrategyChange: (strategyId: string) => void;
}

export function StrategyDecisionPanel({
  strategies,
  selectedStrategyId,
  evaluation,
  loading,
  error,
  onStrategyChange,
}: Props) {
  const decision = evaluation?.decisions[0] ?? null;
  const proposal = evaluation?.proposals[0] ?? null;
  return (
    <div
      className="absolute bottom-10 right-2 z-20 w-[min(24rem,calc(100%-1rem))] rounded border border-terminal-border bg-terminal-panel/95 p-3 text-[11px] text-terminal-text shadow-xl"
      data-testid="strategy-decision-panel"
    >
      <div className="flex items-center justify-between gap-2">
        <div>
          <div className="text-[10px] uppercase tracking-[0.18em] text-terminal-accent">Strategy</div>
          <div className="font-semibold">Deterministic Inspector</div>
        </div>
        <select
          className="max-w-44 rounded border border-terminal-border bg-terminal-bg px-2 py-1 text-[10px]"
          value={selectedStrategyId}
          onChange={(event) => onStrategyChange(event.target.value)}
        >
          {strategies.map((strategy) => (
            <option key={strategy.strategy_id} value={strategy.strategy_id}>
              {strategy.name}
            </option>
          ))}
        </select>
      </div>
      {loading ? <div className="mt-3 text-terminal-muted">Evaluating rules...</div> : null}
      {error ? <div className="mt-3 text-terminal-neg">{error}</div> : null}
      {!loading && !error && decision ? (
        <div className="mt-3 space-y-2">
          <div className="flex items-center justify-between rounded border border-terminal-border/70 bg-terminal-bg/50 px-2 py-1">
            <span className="uppercase tracking-[0.16em] text-terminal-muted">{decision.decision_type}</span>
            <span className={decision.direction === "long" ? "text-terminal-accent" : decision.direction === "short" ? "text-terminal-neg" : "text-terminal-muted"}>
              {decision.direction}
            </span>
          </div>
          <div className="text-terminal-muted">{decision.explanation}</div>
          {evaluation?.warnings.length ? (
            <div className="rounded border border-terminal-warn/60 bg-terminal-warn/10 px-2 py-1 text-terminal-warn">
              {evaluation.warnings.join("; ")}
            </div>
          ) : null}
          {proposal ? (
            <div className="rounded border border-terminal-border/70 bg-terminal-bg/50 px-2 py-2">
              <div className="flex justify-between gap-2">
                <span>Reference</span>
                <span>{proposal.reference_price.toFixed(2)}</span>
              </div>
              <div className="flex justify-between gap-2">
                <span>Invalidation</span>
                <span>{proposal.invalidation_price?.toFixed(2) ?? "n/a"}</span>
              </div>
              {proposal.target_levels.slice(0, 2).map((target, index) => (
                <div className="flex justify-between gap-2" key={`${target.type}-${index}`}>
                  <span>Target {index + 1}</span>
                  <span>{target.price.toFixed(2)}</span>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
      {!loading && !error && !decision ? <div className="mt-3 text-terminal-muted">No evaluation available.</div> : null}
    </div>
  );
}
