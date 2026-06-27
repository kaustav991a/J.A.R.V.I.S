import { motion, useMotionValue, useTransform, animate } from "framer-motion";
import { useLayoutEffect, useRef } from "react";
import { createPortal } from "react-dom";
import "./ScanlineTransition.scss";

/**
 * Phase 8.5: Full-viewport optical scanline; immersive panel content reveals in sync.
 * Beam + curtain are portaled to document.body so they span the entire screen (not the widget box).
 */
export default function ScanlineTransition({ active = true, children }) {
  const lineTop = useMotionValue(50);
  const lineOpacity = useMotionValue(0);
  const clipBottom = useMotionValue(100);

  const contentClipPath = useTransform(clipBottom, (v) => `inset(0 0 ${v}% 0)`);
  /** Inverse of content clip: full-screen veil until the sweep opens the HUD. */
  const curtainClipPath = useTransform(clipBottom, (v) => `inset(0 0 ${100 - v}% 0)`);
  const beamTop = useTransform(lineTop, (v) => `${v}%`);

  const playbackRef = useRef([]);

  useLayoutEffect(() => {
    playbackRef.current.forEach((p) => p.stop());
    playbackRef.current = [];

    if (!active) {
      lineTop.set(50);
      lineOpacity.set(0);
      clipBottom.set(100);
      return;
    }

    let cancelled = false;

    lineTop.set(50);
    clipBottom.set(100);
    lineOpacity.set(1);

    const chain = async () => {
      const toTop = animate(lineTop, 0, {
        duration: 0.35,
        ease: [0.42, 0, 0.58, 1],
      });
      playbackRef.current.push(toTop);
      await toTop;
      if (cancelled) return;

      const sweepLine = animate(lineTop, 100, {
        duration: 0.8,
        ease: [0.42, 0, 0.58, 1],
      });
      const sweepClip = animate(clipBottom, 0, {
        duration: 0.8,
        ease: [0.42, 0, 0.58, 1],
      });
      playbackRef.current.push(sweepLine, sweepClip);
      await Promise.all([sweepLine, sweepClip]);
      if (cancelled) return;

      clipBottom.set(0);
      const fadeBeam = animate(lineOpacity, 0, {
        duration: 0.3,
        ease: [0.42, 0, 0.58, 1],
      });
      playbackRef.current.push(fadeBeam);
      await fadeBeam;
    };

    chain();

    return () => {
      cancelled = true;
      playbackRef.current.forEach((p) => p.stop());
      playbackRef.current = [];
    };
  }, [active, lineTop, lineOpacity, clipBottom]);

  const fullscreenOverlay =
    typeof document !== "undefined" ? (
      <>
        <motion.div
          className="scanline-transition__curtain"
          aria-hidden
          style={{ clipPath: curtainClipPath }}
        />
        <motion.div
          className="scanline-transition__beam scanline-transition__beam--viewport"
          aria-hidden
          style={{
            top: beamTop,
            opacity: lineOpacity,
            y: "-50%",
          }}
        />
      </>
    ) : null;

  return (
    <div className="scanline-transition">
      <motion.div className="scanline-transition__content" style={{ clipPath: contentClipPath }}>
        {children}
      </motion.div>
      {active && fullscreenOverlay
        ? createPortal(fullscreenOverlay, document.body)
        : null}
    </div>
  );
}
