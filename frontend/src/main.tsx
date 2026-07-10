import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { initAuth } from "./auth";
import "./styles.css";

const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("Root element #root not found");
}

// Must resolve before the first render in Entra mode: it completes MSAL's
// redirect-response handling for a sign-in that just finished. No-op in dev.
initAuth().then(() => {
  ReactDOM.createRoot(rootEl).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
});
