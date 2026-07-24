import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.jsx";
import NotchView from "./NotchView.jsx";
import SidecarView from "./SidecarView.jsx";

// Hash router: the Electron shell opens two frameless windows that load
// #/notch and #/sidecar (see electron/main.js). A plain browser at "/"
// (or any other hash) gets the full HUD dashboard.
function viewForHash() {
  const route = (window.location.hash || "").replace(/^#\/?/, "").toLowerCase();
  if (route === "notch") return <NotchView />;
  if (route === "sidecar") return <SidecarView />;
  return <App />;
}

const root = createRoot(document.getElementById("root"));
const render = () => root.render(viewForHash());

render();
window.addEventListener("hashchange", render);
