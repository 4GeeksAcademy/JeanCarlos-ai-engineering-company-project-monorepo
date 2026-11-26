import type { NextConfig } from "next";

// Security headers of the staff backoffice (OWASP A05 Security Misconfiguration).
// - CSP: only this origin, except the chat WebSocket of the Compliance assistant, which the browser
//   opens directly against the API (a Next route cannot proxy a WebSocket upgrade). No plugins, no
//   framing (clickjacking), forms and <base> pinned to this origin. Next's App Router hydrates with
//   inline scripts, hence 'unsafe-inline' for scripts; 'unsafe-eval' only in development (React
//   Refresh). connect-src keeps an injected script from exfiltrating to any other host.
// - HSTS only in production (served over HTTPS behind the reverse proxy).
const dev = process.env.NODE_ENV !== "production";
const apiUrl = process.env.SUPPLIERS_API_URL ?? "http://127.0.0.1:8000";
const chatSocket = process.env.AGENT_WS_URL ?? `${apiUrl.replace(/^http/, "ws")}/agent/chat/ws`;
const chatOrigin = new URL(chatSocket).origin;

const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self'",
  `connect-src 'self' ${chatOrigin}`,
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: csp },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  ...(dev ? [] : [{ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" }]),
];

const nextConfig: NextConfig = {
  poweredByHeader: false, // do not advertise the framework
  async headers() {
    return [
      { source: "/:path*", headers: securityHeaders },
      {
        source: "/reset-password",
        headers: [{ key: "Referrer-Policy", value: "no-referrer" }],
      },
    ];
  },
};

export default nextConfig;
