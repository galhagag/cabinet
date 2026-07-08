export default function LoopBudgetBanner({
  status,
  cyclesUsed,
  cycleLimit,
  onResume,
  resuming,
}: {
  status: string;
  cyclesUsed: number;
  cycleLimit: number;
  onResume: () => void;
  resuming: boolean;
}) {
  const paused = status === "paused_awaiting_human";
  const pct = cycleLimit > 0 ? Math.min(100, Math.round((cyclesUsed / cycleLimit) * 100)) : 0;

  return (
    <div className={`loop-banner ${paused ? "loop-banner-paused" : ""}`}>
      <div className="loop-meter" title={`Autonomous cycles used: ${cyclesUsed} of ${cycleLimit}`}>
        <span className="loop-meter-label">
          Loop budget {cyclesUsed}/{cycleLimit}
        </span>
        <div className="loop-meter-track" role="progressbar" aria-valuenow={cyclesUsed} aria-valuemin={0} aria-valuemax={cycleLimit}>
          <div
            className={`loop-meter-fill ${pct >= 100 ? "loop-meter-full" : ""}`}
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>
      {paused && (
        <div className="paused-alert">
          <span className="paused-text">
            Agents paused — 6-turn autonomous budget reached. Post a message or resume.
          </span>
          <button className="btn btn-resume" onClick={onResume} disabled={resuming}>
            {resuming ? "Resuming…" : "Resume"}
          </button>
        </div>
      )}
    </div>
  );
}
