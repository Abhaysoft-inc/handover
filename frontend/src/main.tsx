import "@fontsource-variable/inter";
import "@fontsource-variable/source-serif-4";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";
import { connectEvents } from "./live";

connectEvents();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
