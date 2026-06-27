import { memo } from "react";
import { motion } from "framer-motion";
import { X } from "lucide-react";

/**
 * Iron Man–style HUD panel for structured backend payloads:
 * { ui_action: 'render_file_list' | 'render_process_list', data: [...] }
 *
 * @param {{ ui_action: string, data: unknown[] } | null} data
 * @param {() => void} onClose
 */
/**
 * Inline-SVG chart for `render_chart` payloads — no external chart lib (CSP-safe).
 * points: [{ label, value }, ...]; type: 'bar' | 'line' | 'pie'.
 */
function HudChart({ type = "bar", points = [] }) {
  const data = (Array.isArray(points) ? points : [])
    .map((p) => ({ label: String(p?.label ?? ""), value: Number(p?.value) || 0 }))
    .slice(0, 24);
  if (!data.length) {
    return <p className="px-2 font-mono text-xs text-cyan-300/50">No data to plot.</p>;
  }
  const W = 380;
  const H = 220;
  const max = Math.max(...data.map((d) => d.value), 1);
  const CYAN = "#22d3ee";

  if (type === "pie") {
    const total = data.reduce((s, d) => s + Math.max(d.value, 0), 0) || 1;
    let acc = 0;
    const cx = 110, cy = 110, r = 90;
    const slices = data.map((d, i) => {
      const frac = Math.max(d.value, 0) / total;
      const a0 = acc * 2 * Math.PI - Math.PI / 2;
      acc += frac;
      const a1 = acc * 2 * Math.PI - Math.PI / 2;
      const large = frac > 0.5 ? 1 : 0;
      const x0 = cx + r * Math.cos(a0), y0 = cy + r * Math.sin(a0);
      const x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1);
      const hue = (i * 47) % 360;
      return (
        <path key={i} d={`M${cx},${cy} L${x0},${y0} A${r},${r} 0 ${large} 1 ${x1},${y1} Z`}
          fill={`hsl(${180 + hue / 3}, 80%, ${45 + (i % 4) * 8}%)`} stroke="#0b1220" strokeWidth="1.5" />
      );
    });
    return (
      <div className="px-1">
        <svg viewBox="0 0 220 220" className="mx-auto block w-[70%]">{slices}</svg>
        <ul className="mt-3 space-y-1">
          {data.map((d, i) => (
            <li key={i} className="flex justify-between font-mono text-xs text-cyan-100/85">
              <span className="truncate">{d.label}</span>
              <span className="tabular-nums text-cyan-300/75">{d.value}</span>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  if (type === "line") {
    const stepX = W / Math.max(data.length - 1, 1);
    const pts = data.map((d, i) => `${i * stepX},${H - (d.value / max) * (H - 30) - 10}`);
    return (
      <div className="px-1">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
          <polyline fill="none" stroke={CYAN} strokeWidth="2" points={pts.join(" ")} />
          {data.map((d, i) => (
            <circle key={i} cx={i * stepX} cy={H - (d.value / max) * (H - 30) - 10} r="3" fill={CYAN} />
          ))}
        </svg>
        <div className="mt-1 flex justify-between font-mono text-[9px] text-cyan-300/50">
          <span>{data[0].label}</span><span>{data[data.length - 1].label}</span>
        </div>
      </div>
    );
  }

  // bar (default)
  const bw = W / data.length;
  return (
    <div className="px-1">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
        {data.map((d, i) => {
          const h = (d.value / max) * (H - 34);
          return (
            <g key={i}>
              <rect x={i * bw + bw * 0.15} y={H - h - 18} width={bw * 0.7} height={h}
                fill={CYAN} opacity="0.8" rx="2" />
              <text x={i * bw + bw / 2} y={H - 4} textAnchor="middle"
                fill="#7dd3fc" fontSize="8" fontFamily="monospace">
                {d.label.length > 6 ? d.label.slice(0, 6) : d.label}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function DataOverlay({ data, onClose }) {
  if (!data || !data.ui_action) return null;

  const rows = Array.isArray(data.data) ? data.data : [];

  const isChart = data.ui_action === "render_chart";

  const title =
    data.ui_action === "render_file_list"
      ? "FILE MANIFEST"
      : data.ui_action === "render_process_list"
        ? "PROCESS MATRIX"
        : isChart
          ? String(data.title || "DATA VISUAL").toUpperCase()
          : "DATA FEED";

  const subtitle =
    data.ui_action === "render_file_list"
      ? "ARCHIVE INDEX // LOCAL VOLUME"
      : data.ui_action === "render_process_list"
        ? "RUNTIME SIGNATURE // MEMORY FOOTPRINT"
        : isChart
          ? `VISUAL ANALYSIS // ${String(data.chart_type || "bar").toUpperCase()}`
          : `${data.ui_action.toUpperCase().replace(/_/g, " ")}`;

  return (
    <motion.div
      className="fixed inset-0 z-[180] flex justify-end bg-black/55 backdrop-blur-[2px]"
      role="presentation"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.22 }}
      onClick={onClose}
    >
      <motion.aside
        role="dialog"
        aria-modal="true"
        aria-labelledby="data-overlay-title"
        className="pointer-events-auto mt-[max(4vh,1rem)] mb-[max(4vh,1rem)] mr-[max(2vw,0.75rem)] flex h-[min(92vh,calc(100%-2rem))] w-[min(100%,420px)] max-w-[92vw] flex-col rounded-l-xl border border-cyan-400/35 bg-black/80 shadow-[0_0_60px_-12px_rgba(34,211,238,0.35)] backdrop-blur-xl"
        initial={{ x: "100%" }}
        animate={{ x: 0 }}
        exit={{ x: "100%" }}
        transition={{ type: "spring", damping: 30, stiffness: 320 }}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex shrink-0 items-start justify-between gap-3 border-b border-cyan-500/25 px-5 py-4">
          <div>
            <p className="font-mono text-[10px] tracking-[0.35em] text-cyan-400/70">
              {subtitle}
            </p>
            <h2
              id="data-overlay-title"
              className="mt-1 font-mono text-lg font-semibold tracking-[0.12em] text-cyan-100"
            >
              {title}
            </h2>
            <p className="mt-1 font-mono text-[11px] text-cyan-300/45">
              {rows.length} record{rows.length === 1 ? "" : "s"}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-cyan-400/30 bg-cyan-400/10 p-2 text-cyan-200 transition hover:bg-cyan-400/20 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/80"
            aria-label="Dismiss data overlay"
          >
            <X className="h-4 w-4" strokeWidth={2.25} />
          </button>
        </header>

        <div className="scrollbar-data-overlay min-h-0 flex-1 overflow-y-auto px-3 py-3">
          {isChart && <HudChart type={data.chart_type} points={rows} />}
          {!isChart && (
          <ul className="space-y-2 pr-1">
            {data.ui_action === "render_file_list" &&
              rows.map((item, index) => (
                <li
                  key={item?.name ? `${item.name}-${index}` : `file-${index}`}
                  className="rounded-lg border border-cyan-500/15 bg-cyan-400/5 px-3 py-2.5 font-mono text-sm text-cyan-50/95"
                >
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="min-w-0 flex-1 truncate text-cyan-100" title={String(item?.name ?? "")}>
                      {String(item?.name ?? "—")}
                    </span>
                    <span className="shrink-0 text-xs tabular-nums text-cyan-300/75">
                      {String(item?.size ?? "—")}
                    </span>
                  </div>
                </li>
              ))}

            {data.ui_action === "render_process_list" &&
              rows.map((item, index) => (
                <li
                  key={
                    item?.process_name
                      ? `${item.process_name}-${index}`
                      : `proc-${index}`
                  }
                  className="rounded-lg border border-cyan-500/15 bg-cyan-400/5 px-3 py-2.5 font-mono text-sm text-cyan-50/95"
                >
                  <div className="flex items-baseline justify-between gap-3">
                    <span
                      className="min-w-0 flex-1 truncate text-cyan-100"
                      title={String(item?.process_name ?? "")}
                    >
                      {String(item?.process_name ?? "—")}
                    </span>
                    <span className="shrink-0 text-xs tabular-nums text-cyan-300/75">
                      {String(item?.memory ?? "—")}
                    </span>
                  </div>
                </li>
              ))}

            {data.ui_action !== "render_file_list" &&
              data.ui_action !== "render_process_list" &&
              rows.map((item, index) => (
                <li
                  key={`raw-${index}`}
                  className="rounded-lg border border-amber-400/20 bg-amber-400/5 px-3 py-2 font-mono text-xs text-amber-100/90"
                >
                  {typeof item === "object" && item !== null
                    ? JSON.stringify(item)
                    : String(item)}
                </li>
              ))}
          </ul>
          )}
        </div>

        <footer className="shrink-0 border-t border-cyan-500/20 px-5 py-3">
          <p className="font-mono text-[10px] tracking-widest text-cyan-400/40">
            J.A.R.V.I.S. // SECURE CHANNEL
          </p>
        </footer>
      </motion.aside>
    </motion.div>
  );
}

export default memo(DataOverlay);
