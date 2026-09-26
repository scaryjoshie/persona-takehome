import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";

// No StrictMode: Framework7's bindings do not survive the simulated double mount.
createRoot(document.getElementById("root")!).render(<App />);
