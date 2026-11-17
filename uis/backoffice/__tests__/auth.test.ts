/**
 * src/lib/auth.ts — client-side password validation and the auth request helper.
 */
import { authRequest, passwordError } from "@/lib/auth";
import { captureAuthentication, track } from "@/services/telemetry";

jest.mock("@/services/telemetry", () => ({ captureAuthentication: jest.fn(), track: jest.fn() }));

describe("passwordError", () => {
  it("accepts a valid password that matches its confirmation", () => {
    expect(passwordError("Secret123!", "Secret123!")).toBe("");
  });

  it("accepts exactly 8 characters and exactly 72 UTF-8 bytes", () => {
    expect(passwordError("12345678", "12345678")).toBe("");
    // "é" is 2 bytes in UTF-8: 36 of them hit bcrypt's 72-byte limit exactly.
    expect(passwordError("é".repeat(36), "é".repeat(36))).toBe("");
  });

  it("counts characters, not UTF-16 units, for the minimum length", () => {
    // 7 emoji are 14 UTF-16 code units but only 7 characters: still too short.
    expect(passwordError("😀".repeat(7), "😀".repeat(7))).toMatch(/al menos 8/);
  });

  it.each([
    ["empty", "", ""],
    ["7 characters", "1234567", "1234567"],
  ])("rejects a password that is too short (%s)", (_case, password, confirmation) => {
    expect(passwordError(password, confirmation)).toMatch(/al menos 8/);
  });

  it("rejects passwords over 72 bytes that bcrypt would silently truncate", () => {
    expect(passwordError("é".repeat(37), "é".repeat(37))).toMatch(/72 bytes/);
  });

  it("rejects a confirmation that does not match", () => {
    expect(passwordError("Secret123!", "Secret123?")).toBe("Las contraseñas no coinciden.");
  });
});

describe("authRequest", () => {
  const fetchMock = jest.fn();
  beforeEach(() => { global.fetch = fetchMock; });

  it("posts JSON with a correlation id and reports the result to telemetry", async () => {
    const response = new Response("{}", { status: 200 });
    fetchMock.mockResolvedValue(response);

    await expect(authRequest("forgot-password", { email: "a@example.org" })).resolves.toBe(response);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/auth/forgot-password");
    expect(JSON.parse(init.body)).toEqual({ email: "a@example.org" });
    expect(init.headers["X-Request-ID"]).toMatch(/^[0-9a-f-]{36}$/);
    expect(captureAuthentication).toHaveBeenCalledWith("forgot-password", response);
  });

  it("returns error responses to the caller instead of throwing", async () => {
    // The form decides how to show a 401; the helper must not swallow it.
    fetchMock.mockResolvedValue(new Response("{}", { status: 401 }));
    expect((await authRequest("login", {})).status).toBe(401);
  });

  it("records a failed login and rethrows when the network is down", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(authRequest("login", {})).rejects.toThrow("Failed to fetch");
    expect(track).toHaveBeenCalledWith("auth_login_failed", { failure_reason: "service_unavailable" });
  });

  it("does not record a login failure for other actions", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(authRequest("reset-password", {})).rejects.toThrow();
    expect(track).not.toHaveBeenCalled();
  });
});
