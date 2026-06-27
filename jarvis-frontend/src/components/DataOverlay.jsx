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
function DataOverlay({ data, onClose }) {
  if (!data || !data.ui_action) return null;

  const rows = Array.isArray(data.data) ? data.data : [];

  const title =
    data.ui_action === "render_file_list"
      ? "FILE MANIFEST"
      : data.ui_action === "render_process_list"
        ? "PROCESS MATRIX"
        : "DATA FEED";

  const subtitle =
    data.ui_action === "render_file_list"
      ? "ARCHIVE INDEX // LOCAL VOLUME"
      : data.ui_action === "render_process_list"
        ? "RUNTIME SIGNATURE // MEMORY FOOTPRINT"
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
