import React, { useState, useEffect } from "react";

const NotepadWidget = () => {
  const [note, setNote] = useState("");
  const [isArchiving, setIsArchiving] = useState(false);

  useEffect(() => {
    const saved = localStorage.getItem("jarvis_notepad");
    if (saved) setNote(saved);
  }, []);

  // Debounced save for matrix effect
  useEffect(() => {
    const timer = setTimeout(() => {
      if (note !== localStorage.getItem("jarvis_notepad") && note.length > 0) {
        localStorage.setItem("jarvis_notepad", note);
        setIsArchiving(true);
        setTimeout(() => setIsArchiving(false), 1500);
      }
    }, 1000);
    return () => clearTimeout(timer);
  }, [note]);

  const handleChange = (e) => {
    setNote(e.target.value);
  };

  return (
    <div className="notepad-ui holographic-ui">
      <textarea
        className="notepad-textarea"
        value={note}
        onChange={handleChange}
        placeholder="Enter notes..."
        spellCheck="false"
      />
      {isArchiving && (
        <div className="archiving-overlay">
          <div className="matrix-rain">
            {Array.from({ length: 15 }).map((_, i) => (
              <span key={i} style={{ animationDelay: `${Math.random()}s`, left: `${Math.random() * 100}%` }}>
                {Math.random() > 0.5 ? '1' : '0'}
              </span>
            ))}
          </div>
          <div className="archived-text">DATA ENCRYPTED & ARCHIVED</div>
        </div>
      )}
    </div>
  );
};

export default NotepadWidget;
