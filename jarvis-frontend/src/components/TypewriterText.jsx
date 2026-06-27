import React, { useState, useEffect, memo } from "react";

/** Long JARVIS log lines skip typing to avoid dozens of React commits per message. */
const INSTANT_THRESHOLD = 280;

const TypewriterText = memo(function TypewriterText({ text, speed = 30 }) {
  const [displayedText, setDisplayedText] = useState("");

  useEffect(() => {
    if (!text) {
      setDisplayedText("");
      return;
    }

    if (text.length >= INSTANT_THRESHOLD) {
      setDisplayedText(text);
      return;
    }

    let i = 0;
    setDisplayedText("");

    const tickMs = Math.max(24, speed);
    const charsPerTick = Math.max(1, Math.round(22 / Math.max(8, speed)));

    const timer = setInterval(() => {
      i = Math.min(i + charsPerTick, text.length);
      setDisplayedText(text.slice(0, i));
      if (i >= text.length) {
        clearInterval(timer);
      }
    }, tickMs);

    return () => clearInterval(timer);
  }, [text, speed]);

  return <>{displayedText}</>;
});

export default TypewriterText;
