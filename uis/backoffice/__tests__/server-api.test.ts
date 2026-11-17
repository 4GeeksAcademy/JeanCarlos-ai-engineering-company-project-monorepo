/**
 * src/lib/server-api.ts — API URL building, CSRF origin check and the session guard.
 */
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { apiUrl, requireSession, sameOrigin } from "@/lib/server-api";

// Next.js resolves "server-only" internally; outside Next it is a virtual module.
jest.mock("server-only", () => ({}), { virtual: true });
jest.mock("next/headers", () => ({ cookies: jest.fn() }));
// Like Next.js, redirect() interrupts the function by throwing.
jest.mock("next/navigation", () => ({
  redirect: jest.fn((path: string) => { throw new Error(`REDIRECT ${path}`); }),
}));

function request(headers: Record<string, string>, url = "http://localhost:3001/api/suppliers") {
  return new Request(url, { method: "POST", headers });
}

describe("apiUrl", () => {
  const original = process.env.SUPPLIERS_API_URL;
  afterEach(() => { process.env.SUPPLIERS_API_URL = original; });

  it("joins the configured base with the path without doubling slashes", () => {
    process.env.SUPPLIERS_API_URL = "http://api:8000/";
    expect(apiUrl("/auth/me")).toBe("http://api:8000/auth/me");
  });

  it("falls back to the local API when nothing is configured", () => {
    delete process.env.SUPPLIERS_API_URL;
    expect(apiUrl("/users")).toBe("http://127.0.0.1:8000/users");
  });
});

describe("sameOrigin", () => {
  it("accepts a request whose Origin matches the Host", () => {
    expect(sameOrigin(request({ origin: "http://localhost:3001", host: "localhost:3001" }))).toBe(true);
  });

  it("honours the protocol reported by a TLS-terminating proxy", () => {
    const behindProxy = request(
      { origin: "https://app.example.org", host: "app.example.org", "x-forwarded-proto": "https" },
      "http://app.example.org/api/suppliers",
    );
    expect(sameOrigin(behindProxy)).toBe(true);
  });

  it.each([
    ["another site", { origin: "https://evil.example", host: "localhost:3001" }],
    ["a different port", { origin: "http://localhost:3000", host: "localhost:3001" }],
    ["a protocol downgrade", { origin: "http://app.example.org", host: "app.example.org", "x-forwarded-proto": "https" }],
    ["a missing Origin", { host: "localhost:3001" }],
    ["a malformed Origin", { origin: "not a url", host: "localhost:3001" }],
    ["an Origin with a path", { origin: "http://localhost:3001/x", host: "localhost:3001" }],
  ])("rejects %s (CSRF protection)", (_case, headers) => {
    expect(sameOrigin(request(headers))).toBe(false);
  });
});

describe("requireSession", () => {
  const fetchMock = jest.fn();
  const withCookie = (value?: string) =>
    (cookies as jest.Mock).mockResolvedValue({ get: () => (value ? { value } : undefined) });

  beforeEach(() => {
    global.fetch = fetchMock;
    process.env.SUPPLIERS_API_URL = "http://api:8000";
  });

  it("verifies the session token against GET /auth/me", async () => {
    withCookie("token-123");
    fetchMock.mockResolvedValue(new Response("{}", { status: 200 }));

    await expect(requireSession()).resolves.toBeUndefined();
    const [url, init] = fetchMock.mock.calls[0];
    // Regression: the API moved the identity route from /me to /auth/me (AUTH-01).
    expect(url).toBe("http://api:8000/auth/me");
    expect(init.headers).toEqual({ Authorization: "Bearer token-123" });
  });

  it("redirects to /login without calling the API when there is no session cookie", async () => {
    withCookie(undefined);
    await expect(requireSession()).rejects.toThrow("REDIRECT /login");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("redirects to /login when the API rejects the token", async () => {
    withCookie("expired");
    fetchMock.mockResolvedValue(new Response("{}", { status: 401 }));
    await expect(requireSession()).rejects.toThrow("REDIRECT /login");
    expect(redirect).toHaveBeenCalledWith("/login");
  });

  it.each([
    ["the API is unreachable", () => fetchMock.mockRejectedValue(new TypeError("fetch failed"))],
    ["the API fails", () => fetchMock.mockResolvedValue(new Response("{}", { status: 503 }))],
  ])("raises a generic error when %s, without logging the token", async (_case, arrange) => {
    withCookie("secret-token");
    const log = jest.spyOn(console, "error").mockImplementation(() => {});
    arrange();
    await expect(requireSession()).rejects.toThrow("No se pudo verificar la sesión.");
    expect(JSON.stringify(log.mock.calls)).not.toContain("secret-token");
  });
});
