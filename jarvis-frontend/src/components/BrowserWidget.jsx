import React, { useState, useEffect } from "react";
import { Search, ExternalLink, ShieldAlert } from "lucide-react";

const BrowserWidget = ({ externalUrl, immersive = false }) => {
  const defaultUrl = "https://www.youtube.com/embed/S2O6oV_2H8k?autoplay=1&mute=1";
  const [urlInput, setUrlInput] = useState(externalUrl || defaultUrl);
  const [currentUrl, setCurrentUrl] = useState(externalUrl || defaultUrl);
  const [isEstablishing, setIsEstablishing] = useState(true);
  // Some frames error outright (bad host, network failure) — onError fires and
  // we swap to a fallback. NOTE: X-Frame-Options / CSP blocks are NOT reliably
  // detectable client-side (the browser fires onLoad and renders an
  // inspection-proof error page), so the always-present "open externally"
  // button below is the real escape hatch, not this flag.
  const [loadError, setLoadError] = useState(false);

  const openExternally = () => {
    try {
      window.open(currentUrl, "_blank", "noopener,noreferrer");
    } catch (e) { /* noop */ }
  };

  // Incoming Transmission Effect.
  useEffect(() => {
    setIsEstablishing(true);
    setLoadError(false);
    const timer = setTimeout(() => setIsEstablishing(false), 2500);
    return () => clearTimeout(timer);
  }, [currentUrl]);

  const handleFrameError = () => setLoadError(true);

  useEffect(() => {
    if (externalUrl) {
      let finalUrl = externalUrl.trim();
      if (finalUrl.includes("m.youtube.com")) {
        finalUrl = finalUrl.replace("m.youtube.com", "www.youtube.com");
      }
      if (finalUrl.includes("youtube.com/watch?v=")) {
        finalUrl = finalUrl.replace("watch?v=", "embed/") + "?autoplay=1";
      } else if (finalUrl.includes("youtu.be/")) {
        finalUrl = finalUrl.replace("youtu.be/", "www.youtube.com/embed/") + "?autoplay=1";
      }
      setUrlInput(finalUrl);
      setCurrentUrl(finalUrl);
    }
  }, [externalUrl]);

  const handleSubmit = (e) => {
    e.preventDefault();
    let finalUrl = urlInput.trim();

    // Auto-convert standard YouTube links to embed links
    if (finalUrl.includes("youtube.com/watch?v=")) {
      finalUrl = finalUrl.replace("watch?v=", "embed/");
    } else if (finalUrl.includes("youtu.be/")) {
      finalUrl = finalUrl.replace("youtu.be/", "www.youtube.com/embed/");
    }

    // Check if it's a URL or a search query
    if (!finalUrl.includes(".") && !finalUrl.startsWith("http")) {
      // It's a search query, let's use Wikipedia (allows iframes)
      finalUrl = `https://en.wikipedia.org/wiki/Special:Search?search=${encodeURIComponent(finalUrl)}`;
    } else if (!finalUrl.startsWith("http")) {
      finalUrl = "https://" + finalUrl;
    }

    setCurrentUrl(finalUrl);
  };

  return (
    <div className={`browser-ui holographic-ui${immersive ? " browser-ui--immersive" : ""}`}>
      <form onSubmit={handleSubmit} className="browser-header">
        <Search size={14} color="#00ffcc" />
        <input
          type="text"
          value={urlInput}
          onChange={(e) => setUrlInput(e.target.value)}
          placeholder="ENTER SECURE URL..."
          className="browser-input"
        />
        <button
          type="button"
          onClick={openExternally}
          className="browser-external-btn"
          title="Open in external browser"
          aria-label="Open in external browser"
        >
          <ExternalLink size={14} color="#00ffcc" />
        </button>
      </form>
      <div className="browser-frame-container">
        {isEstablishing ? (
          <div className="comm-link-overlay">
            <div className="waveform">
              <div className="bar"></div><div className="bar"></div><div className="bar"></div><div className="bar"></div><div className="bar"></div>
            </div>
            <div className="comm-text">ESTABLISHING SECURE COMM LINK...</div>
            <div className="glitch-overlay"></div>
          </div>
        ) : loadError ? (
          <div className="browser-fallback">
            <ShieldAlert size={34} color="#ffb020" />
            <div className="browser-fallback__title">FEED REFUSED CONNECTION</div>
            <div className="browser-fallback__url" title={currentUrl}>{currentUrl}</div>
            <p className="browser-fallback__note">
              This destination blocks embedded framing.
            </p>
            <button type="button" onClick={openExternally} className="browser-fallback__btn">
              <ExternalLink size={14} /> OPEN EXTERNALLY
            </button>
          </div>
        ) : (
          <iframe
            src={currentUrl}
            title="JARVIS Secure Browser"
            frameBorder="0"
            onError={handleFrameError}
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
            allowFullScreen
            className="browser-iframe fade-in"
          ></iframe>
        )}
      </div>
    </div>
  );
};

export default BrowserWidget;
