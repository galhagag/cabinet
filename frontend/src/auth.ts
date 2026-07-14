// Microsoft Entra ID sign-in (MSAL) — real auth for CABINET_AUTH_MODE=entra.
//
// VITE_AUTH_MODE=dev (default): no-op: the app keeps using the X-User-Email
// header via api.ts's getUserEmail(). VITE_AUTH_MODE=entra: this module
// drives the MSAL redirect login flow and hands api.ts/ws.ts a verified
// access token for the backend's own API app registration — the backend
// validates it against the tenant's JWKS (see backend/app/services/entra_auth.py).
import {
  InteractionRequiredAuthError,
  PublicClientApplication,
  type AccountInfo,
} from "@azure/msal-browser";

export const AUTH_MODE: string = (import.meta.env.VITE_AUTH_MODE as string | undefined) ?? "dev";
export const isEntraAuth = AUTH_MODE === "entra";

const TENANT_ID = import.meta.env.VITE_ENTRA_TENANT_ID as string | undefined;
const CLIENT_ID = import.meta.env.VITE_ENTRA_CLIENT_ID as string | undefined;
// The API app registration's exposed scope, e.g. "api://<api-client-id>/access_as_user".
const API_SCOPE = import.meta.env.VITE_ENTRA_API_SCOPE as string | undefined;
const PENDING_INVITE_TOKEN_KEY = "cabinet_pending_invite_token";

let msal: PublicClientApplication | null = null;
let initPromise: Promise<void> | null = null;

function requireConfig(): { tenantId: string; clientId: string; scope: string } {
  if (!TENANT_ID || !CLIENT_ID || !API_SCOPE) {
    throw new Error(
      "VITE_ENTRA_TENANT_ID, VITE_ENTRA_CLIENT_ID and VITE_ENTRA_API_SCOPE must " +
        "all be set when VITE_AUTH_MODE=entra",
    );
  }
  return { tenantId: TENANT_ID, clientId: CLIENT_ID, scope: API_SCOPE };
}

function getMsal(): PublicClientApplication {
  if (!msal) {
    const { tenantId, clientId } = requireConfig();
    msal = new PublicClientApplication({
      auth: {
        clientId,
        authority: `https://login.microsoftonline.com/${tenantId}`,
        redirectUri: window.location.origin,
      },
      cache: { cacheLocation: "localStorage" },
    });
  }
  return msal;
}

// Must run once before rendering: completes MSAL's internal setup and
// resolves the redirect response from a just-finished sign-in.
export function initAuth(): Promise<void> {
  if (!isEntraAuth) return Promise.resolve();
  if (!initPromise) {
    initPromise = (async () => {
      const app = getMsal();
      await app.initialize();
      const result = await app.handleRedirectPromise();
      if (result?.account) {
        app.setActiveAccount(result.account);
      } else if (!app.getActiveAccount()) {
        const [first] = app.getAllAccounts();
        if (first) app.setActiveAccount(first);
      }
    })();
  }
  return initPromise;
}

export function getActiveAccount(): AccountInfo | null {
  if (!isEntraAuth) return null;
  return getMsal().getActiveAccount();
}

export function consumePendingInviteToken(): string | null {
  const token = window.sessionStorage.getItem(PENDING_INVITE_TOKEN_KEY);
  if (token) {
    window.sessionStorage.removeItem(PENDING_INVITE_TOKEN_KEY);
  }
  return token;
}

export async function signIn(): Promise<void> {
  const { scope } = requireConfig();
  const token = new URLSearchParams(window.location.search).get("token");
  if (token) {
    window.sessionStorage.setItem(PENDING_INVITE_TOKEN_KEY, token);
  }
  await getMsal().loginRedirect({ scopes: [scope] });
}

export async function signOut(): Promise<void> {
  await getMsal().logoutRedirect();
}

// Returns a verified access token for the backend API, refreshing silently
// via the cached refresh token; falls back to an interactive redirect only
// when silent acquisition genuinely requires it (consent change, MFA step-up).
export async function getAccessToken(): Promise<string> {
  const { scope } = requireConfig();
  const app = getMsal();
  const account = app.getActiveAccount();
  if (!account) {
    throw new Error("not signed in");
  }
  try {
    const result = await app.acquireTokenSilent({ scopes: [scope], account });
    return result.accessToken;
  } catch (err) {
    if (err instanceof InteractionRequiredAuthError) {
      await app.acquireTokenRedirect({ scopes: [scope], account });
      // Redirect navigates away; nothing left to return on this turn.
      throw new Error("redirecting for interactive sign-in");
    }
    throw err;
  }
}
