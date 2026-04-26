import React, { useState, useEffect, useMemo } from "react";

/**
 * IntroductionCeremony — A cinematic full-screen takeover overlay.
 * Phases:
 *   0: Black screen + particles fade in
 *   1: Arc reactor pulse ring
 *   2: "V.I.P. PROTOCOL INITIATED" text
 *   3: Horizontal divider expands
 *   4: "WELCOME" types out letter by letter
 *   5: "MISS MOUSUMI" types out letter by letter
 *   6: Subtitle fades in
 *   7: Hold (JARVIS is speaking)
 *   8: Fade out
 */
export default function IntroductionCeremony({ isActive, onComplete }) {
  const [phase, setPhase] = useState(-1);
  const [welcomeText, setWelcomeText] = useState("");
  const [nameText, setNameText] = useState("");
  const [isExiting, setIsExiting] = useState(false);

  const WELCOME = "W E L C O M E";
  const NAME = "M I S S   M O U S U M I";

  // Generate particle positions once
  const particles = useMemo(() => {
    return Array.from({ length: 60 }, (_, i) => ({
      id: i,
      left: Math.random() * 100,
      top: Math.random() * 100,
      size: 1 + Math.random() * 3,
      delay: Math.random() * 4,
      duration: 3 + Math.random() * 4,
      drift: -30 + Math.random() * 60,
    }));
  }, []);

  // Phase sequencer
  useEffect(() => {
    if (!isActive) {
      setPhase(-1);
      setWelcomeText("");
      setNameText("");
      setIsExiting(false);
      return;
    }

    setPhase(0);

    const timers = [
      setTimeout(() => setPhase(1), 800),    // Arc reactor pulse
      setTimeout(() => setPhase(2), 2000),   // VIP PROTOCOL text
      setTimeout(() => setPhase(3), 3200),   // Divider line
      setTimeout(() => setPhase(4), 3800),   // WELCOME typewriter
      setTimeout(() => setPhase(5), 6000),   // MISS MOUSUMI typewriter
      setTimeout(() => setPhase(6), 8500),   // Subtitle
      setTimeout(() => setPhase(7), 9500),   // Hold
    ];

    return () => timers.forEach(clearTimeout);
  }, [isActive]);

  // Typewriter for WELCOME
  useEffect(() => {
    if (phase < 4) return;
    let i = 0;
    setWelcomeText("");
    const timer = setInterval(() => {
      i++;
      setWelcomeText(WELCOME.slice(0, i));
      if (i >= WELCOME.length) clearInterval(timer);
    }, 80);
    return () => clearInterval(timer);
  }, [phase >= 4 && isActive]);

  // Typewriter for NAME
  useEffect(() => {
    if (phase < 5) return;
    let i = 0;
    setNameText("");
    const timer = setInterval(() => {
      i++;
      setNameText(NAME.slice(0, i));
      if (i >= NAME.length) clearInterval(timer);
    }, 65);
    return () => clearInterval(timer);
  }, [phase >= 5 && isActive]);

  // Exit handler
  useEffect(() => {
    if (!isActive && phase > 0) {
      setIsExiting(true);
      const t = setTimeout(() => {
        setPhase(-1);
        setIsExiting(false);
        if (onComplete) onComplete();
      }, 1500);
      return () => clearTimeout(t);
    }
  }, [isActive]);

  if (phase < 0 && !isExiting) return null;

  return (
    <div className={`ceremony-overlay ${isExiting ? "ceremony-exit" : "ceremony-enter"}`}>
      {/* Particle field */}
      <div className="ceremony-particles">
        {particles.map((p) => (
          <div
            key={p.id}
            className="ceremony-particle"
            style={{
              left: `${p.left}%`,
              top: `${p.top}%`,
              width: `${p.size}px`,
              height: `${p.size}px`,
              animationDelay: `${p.delay}s`,
              animationDuration: `${p.duration}s`,
              "--drift": `${p.drift}px`,
            }}
          />
        ))}
      </div>

      {/* Arc reactor pulse */}
      {phase >= 1 && (
        <div className="ceremony-reactor">
          <div className="reactor-ring reactor-ring-1" />
          <div className="reactor-ring reactor-ring-2" />
          <div className="reactor-ring reactor-ring-3" />
          <div className="reactor-core" />
        </div>
      )}

      {/* VIP Protocol text */}
      {phase >= 2 && (
        <div className="ceremony-protocol-text">
          <span className="protocol-diamond">◆</span>
          &nbsp;&nbsp;V.I.P. PROTOCOL INITIATED&nbsp;&nbsp;
          <span className="protocol-diamond">◆</span>
        </div>
      )}

      {/* Expanding divider */}
      {phase >= 3 && <div className="ceremony-divider" />}

      {/* WELCOME */}
      {phase >= 4 && (
        <div className="ceremony-welcome">{welcomeText}<span className="ceremony-cursor">|</span></div>
      )}

      {/* MISS MOUSUMI */}
      {phase >= 5 && (
        <div className="ceremony-name">{nameText}<span className="ceremony-cursor gold">|</span></div>
      )}

      {/* Subtitle */}
      {phase >= 6 && (
        <div className="ceremony-subtitle">
          "It is a profound honor to make your acquaintance, Madam."
        </div>
      )}

      {/* Corner brackets */}
      {phase >= 3 && (
        <>
          <div className="ceremony-corner corner-tl" />
          <div className="ceremony-corner corner-tr" />
          <div className="ceremony-corner corner-bl" />
          <div className="ceremony-corner corner-br" />
        </>
      )}

      {/* Scan line */}
      {phase >= 1 && <div className="ceremony-scanline" />}
    </div>
  );
}
