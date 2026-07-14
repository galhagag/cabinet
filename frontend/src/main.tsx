import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { initAuth } from "./auth";
import "./styles.css";

const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("Root element #root not found");
}

const root = ReactDOM.createRoot(rootEl);

function renderApp() {
  root.render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}

function renderBootstrapError(message: string) {
  root.render(
    <React.StrictMode>
      <div className="app">
        <div className="joining-note">
          <h2>Sign-in unavailable</h2>
          <p>{message}</p>
        </div>
      </div>
    </React.StrictMode>,
  );
}

// Must resolve before the first render in Entra mode: it completes MSAL's
// redirect-response handling for a sign-in that just finished. No-op in dev.
initAuth()
  .then(renderApp)
  .catch((err) => {
    renderBootstrapError(
      err instanceof Error ? err.message : "Unknown sign-in bootstrap error",
    );
  });
