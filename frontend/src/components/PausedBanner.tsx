export default function PausedBanner({
  status,
  cycleLimit,
  onResume,
  resuming,
}: {
  status: string;
  cycleLimit: number;
  onResume: () => void;
  resuming: boolean;
}) {
  if (status !== "paused_awaiting_human") return null;

  return (
    <div className="paused-alert">
      <span className="paused-text">
        Agents paused — {cycleLimit}-turn autonomous budget reached. Post a message or resume.
      </span>
      <button className="btn btn-resume" onClick={onResume} disabled={resuming}>
        {resuming ? "Resuming…" : "Resume"}
      </button>
    </div>
  );
}
