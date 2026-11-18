import type { Claim, Clinician } from "../../src/types/models";
import {
  isDenialRateAboveThreshold, isNoShowRateAboveThreshold, validateClaim, validateClinician,
} from "../../src/utils/validations";
import { claim, clinician } from "./fixtures";

describe("business validations", () => {
  beforeEach(() => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date("2026-06-15T12:00:00Z"));
  });

  afterEach(() => jest.useRealTimers());

  test("accepts a claim submitted today and a denied claim with a reason", () => {
    expect(validateClaim(claim(), ["clinic-a"])).toEqual({ valid: true, errors: [] });
    expect(validateClaim(claim({
      status: "denied", denialReason: "coding_error",
    }), ["clinic-a"]).valid).toBe(true);
  });

  test.each<[Partial<Claim>, string]>([
    [{ claimAmount: 0 }, "claimAmount must be greater than 0."],
    [{ claimAmount: -1 }, "claimAmount must be greater than 0."],
    [{ submissionDate: "not-a-date" }, "submissionDate must be a valid ISO 8601 date string."],
    [{ submissionDate: "2026-06-16" }, "submissionDate cannot be in the future."],
    [{ locationId: "unknown" }, "locationId must match a known clinic ID."],
    [{ status: "denied" }, "denialReason is required when status is denied."],
    [{ patientId: "HC-12345" }, "patientId must follow format HC- plus 6 alphanumeric characters."],
  ])("rejects invalid claim fields %j", (overrides, message) => {
    const result = validateClaim(claim(overrides), ["clinic-a"]);
    expect(result.valid).toBe(false);
    expect(result.errors).toContain(message);
  });

  test("reports multiple claim errors together", () => {
    const result = validateClaim(claim({ claimAmount: 0, patientId: "invalid" }), []);
    expect(result.valid).toBe(false);
    expect(result.errors).toHaveLength(3);
  });

  test("accepts zero CME hours and a licence expiring today", () => {
    expect(validateClinician(clinician({
      cmeHoursRequired: 0, cmeHoursLogged: 0, licenceExpiryDate: "2026-06-15",
    }))).toEqual({ valid: true, errors: [] });
  });

  test.each<[Partial<Clinician>, string]>([
    [{ cmeHoursRequired: -1 }, "cmeHoursRequired must be greater than or equal to 0."],
    [{ cmeHoursLogged: -1 }, "cmeHoursLogged must be greater than or equal to 0."],
    [{ licenceExpiryDate: "not-a-date" }, "licenceExpiryDate must be a valid ISO 8601 date string."],
    [{ licenceExpiryDate: "2026-06-14" }, "licenceExpiryDate cannot be in the past."],
  ])("rejects invalid clinician fields %j", (overrides, message) => {
    const result = validateClinician(clinician(overrides));
    expect(result.valid).toBe(false);
    expect(result.errors).toContain(message);
  });
});

describe("alert thresholds", () => {
  test.each([
    [isDenialRateAboveThreshold, 8],
    [isNoShowRateAboveThreshold, 20],
  ])("alerts only strictly above the default or custom threshold", (check, threshold) => {
    expect(check(threshold - 0.01)).toBe(false);
    expect(check(threshold)).toBe(false);
    expect(check(threshold + 0.01)).toBe(true);
    expect(check(50, 50)).toBe(false);
    expect(check(50.01, 50)).toBe(true);
  });
});
