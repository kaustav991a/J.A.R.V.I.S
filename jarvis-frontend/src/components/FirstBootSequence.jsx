import React, { useState, useEffect } from "react";
import "./FirstBootSequence.scss";

export default function FirstBootSequence({ isActive, onComplete }) {
  const [displayedText, setDisplayedText] = useState("");
  const [isExiting, setIsExiting] = useState(false);
  
  const fullText = "Allow me to introduce myself. I am J.A.R.V.I.S., the virtual artificial intelligence. I am here to assist you with a variety of tasks as best I can. 24 hours a day, 7 days a week. Importing all preferences from home interface. Systems are now fully operational.";

  useEffect(() => {
    if (!isActive) return;

    let i = 0;
    setDisplayedText("");
    setIsExiting(false); // Reset exit state for repeat triggers
    
    // Typing effect
    const timer = setInterval(() => {
      i++;
      setDisplayedText(fullText.slice(0, i));
      
      if (i >= fullText.length) {
        clearInterval(timer);
        
        // Wait a few seconds after typing is done, then fade out
        setTimeout(() => {
          setIsExiting(true);
          setTimeout(() => {
            if (onComplete) onComplete();
          }, 2000); // fade out duration
        }, 3000); // hold text duration
      }
    }, 35); // typing speed

    return () => clearInterval(timer);
  }, [isActive]);

  if (!isActive) return null;

  return (
    <div className={`first-boot-sequence ${isExiting ? "fade-out" : "fade-in"}`}>
      <div className="hud-grid"></div>
      <div className="text-container">
        {displayedText}<span className="cursor"></span>
      </div>
    </div>
  );
}
