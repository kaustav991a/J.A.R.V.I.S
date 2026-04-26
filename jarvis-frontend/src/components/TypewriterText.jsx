import React, { useState, useEffect } from "react";

const TypewriterText = ({ text, speed = 30 }) => {
  const [displayedText, setDisplayedText] = useState("");

  useEffect(() => {
    if (!text) {
      setDisplayedText("");
      return;
    }

    let i = 0;
    setDisplayedText("");

    const timer = setInterval(() => {
      i++;
      setDisplayedText(text.slice(0, i));

      if (i >= text.length) {
        clearInterval(timer);
      }
    }, speed);

    return () => clearInterval(timer);
  }, [text, speed]);

  return <>{displayedText}</>;
};

export default TypewriterText;
