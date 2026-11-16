import {
  calculateDenialRate, calculateNoShowCost, denialRateByLocation, denialRateByPayer,
  flagHighDenialPayers, flagHighNoShowLocations, generateCMEReport,
  getCliniciansAtRisk, getCliniciansWithExpiringLicences, noShowRateByLocation,
} from "../../src/utils/transformations";
import { appointment, claim, clinician, location } from "./fixtures";

describe("denial metrics", () => {
  const claims = [
    claim({ status: "denied" }),
    claim(),
    claim({ payerName: "Other Payer", locationId: "clinic-b" }),
  ];

  test("rounds percentages and separates payer and location groups", () => {
    expect(calculateDenialRate(claims)).toBe(33.33);
    expect(denialRateByPayer(claims)).toEqual({ "Test Payer": 50, "Other Payer": 0 });
    expect(denialRateByLocation(claims)).toEqual({ "clinic-a": 50, "clinic-b": 0 });
    expect(flagHighDenialPayers(claims)).toEqual(["Test Payer"]);
    expect(flagHighDenialPayers(claims, 50)).toEqual([]);
  });

  test("rejects a rate without claims but returns empty grouped results", () => {
    expect(() => calculateDenialRate([])).toThrow("empty claims array");
    expect(denialRateByPayer([])).toEqual({});
    expect(denialRateByLocation([])).toEqual({});
    expect(flagHighDenialPayers([])).toEqual([]);
  });
});

describe("no-show metrics", () => {
  test("counts only no-shows in the inclusive seven-day window at the requested clinic", () => {
    const appointments = [
      appointment({ scheduledDate: "2026-06-09", status: "no_show" }),
      appointment({ scheduledDate: "2026-06-15", status: "no_show", serviceType: "specialist" }),
      appointment({ scheduledDate: "2026-06-08", status: "no_show" }),
      appointment({ scheduledDate: "2026-06-16", status: "no_show" }),
      appointment({ status: "no_show", locationId: "clinic-b" }),
      appointment({ status: "cancelled" }),
      appointment(),
    ];
    expect(calculateNoShowCost(appointments, location, "2026-06-15")).toBe(300.25);
    expect(calculateNoShowCost([], location, "2026-06-15")).toBe(0);
  });

  test("includes locations with no absences and applies strict alert thresholds", () => {
    const appointments = [
      appointment({ status: "no_show" }), appointment(), appointment(),
      appointment({ locationId: "clinic-b" }),
    ];
    expect(noShowRateByLocation(appointments)).toEqual({ "clinic-a": 33.33, "clinic-b": 0 });
    expect(flagHighNoShowLocations(appointments)).toEqual(["clinic-a"]);
    expect(flagHighNoShowLocations(appointments, 33.33)).toEqual([]);
    expect(noShowRateByLocation([])).toEqual({});
    expect(flagHighNoShowLocations([])).toEqual([]);
  });
});

describe("CME reports and licence deadlines", () => {
  test("calculates remaining hours, percentages and UTC day counts", () => {
    const [report] = generateCMEReport([clinician({
      cmeHoursRequired: 30, cmeHoursLogged: 10,
    })], "2026-06-15");
    expect(report).toMatchObject({
      clinicianId: "DR-001", fullName: "Test Clinician", role: "physician",
      locationId: "clinic-a", hoursRequired: 30, hoursLogged: 10,
      hoursRemaining: 20, percentComplete: 33.3, daysRemainingInCycle: 199,
      complianceStatus: "on_track", licenceDaysRemaining: 199,
    });
  });

  test("marks completed and zero-requirement cycles complete without negative remaining hours", () => {
    const reports = generateCMEReport([
      clinician({ cmeHoursLogged: 25 }),
      clinician({ cmeHoursRequired: 0, cmeHoursLogged: 0 }),
    ], "2027-01-01");
    expect(reports.map((report) => ({
      remaining: report.hoursRemaining, percent: report.percentComplete, status: report.complianceStatus,
    }))).toEqual([
      { remaining: 0, percent: 125, status: "complete" },
      { remaining: 0, percent: 100, status: "complete" },
    ]);
  });

  test("distinguishes before-cycle, at-risk, end-of-cycle and overdue states", () => {
    const pending = clinician({ cmeHoursLogged: 0 });
    const statusAt = (date: string) => generateCMEReport([pending], date)[0].complianceStatus;
    expect(statusAt("2025-12-31")).toBe("on_track");
    expect(statusAt("2026-01-01")).toBe("on_track");
    expect(statusAt("2026-07-01")).toBe("at_risk");
    expect(statusAt("2026-12-31")).toBe("at_risk");
    expect(statusAt("2027-01-01")).toBe("overdue");
  });

  test("returns only at-risk or overdue clinicians", () => {
    const clinicians = [
      clinician({ clinicianId: "risk", cmeHoursLogged: 0 }),
      clinician({ clinicianId: "overdue", cmeYearStartDate: "2025-01-01" }),
      clinician({ clinicianId: "complete", cmeHoursLogged: 20 }),
      clinician({ clinicianId: "on-track", cmeHoursLogged: 15 }),
    ];
    expect(getCliniciansAtRisk(clinicians, "2026-07-01")).toEqual(clinicians.slice(0, 2));
    expect(generateCMEReport([], "2026-07-01")).toEqual([]);
    expect(getCliniciansAtRisk([], "2026-07-01")).toEqual([]);
  });

  test("includes licences expiring today or at the threshold but excludes expired and later ones", () => {
    const clinicians = [
      clinician({ licenceExpiryDate: "2026-06-14" }),
      clinician({ licenceExpiryDate: "2026-06-15" }),
      clinician({ licenceExpiryDate: "2026-07-15" }),
      clinician({ licenceExpiryDate: "2026-07-16" }),
    ];
    expect(getCliniciansWithExpiringLicences(clinicians, "2026-06-15", 30))
      .toEqual(clinicians.slice(1, 3));
    expect(getCliniciansWithExpiringLicences(clinicians, "2026-06-15", 0))
      .toEqual([clinicians[1]]);
    expect(getCliniciansWithExpiringLicences([], "2026-06-15", 30)).toEqual([]);
  });
});
