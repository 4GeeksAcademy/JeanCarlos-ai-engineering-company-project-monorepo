import { binarySearchClaimById, findClaimById, findClinicianById } from "../../src/utils/search";
import { claim, clinician } from "./fixtures";

describe("claim and clinician searches", () => {
  const claims = [
    claim({ claimId: "CL-001" }),
    claim({ claimId: "CL-002" }),
    claim({ claimId: "CL-003" }),
  ];

  test.each(["CL-001", "CL-002", "CL-003"])("finds %s by linear and binary search", (id) => {
    const index = claims.findIndex((item) => item.claimId === id);
    expect(findClaimById(claims, id)).toBe(claims[index]);
    expect(binarySearchClaimById(claims, id)).toBe(index);
  });

  test.each(["CL-000", "CL-002a", "CL-004"])("reports missing claim %s", (id) => {
    expect(findClaimById(claims, id)).toBeNull();
    expect(binarySearchClaimById(claims, id)).toBe(-1);
  });

  test("handles empty collections and a single claim", () => {
    expect(findClaimById([], "CL-001")).toBeNull();
    expect(binarySearchClaimById([], "CL-001")).toBe(-1);
    expect(binarySearchClaimById([claims[0]], "CL-001")).toBe(0);
    expect(binarySearchClaimById([claims[0]], "CL-002")).toBe(-1);
    expect(findClinicianById([], "DR-001")).toBeNull();
  });

  test("finds the requested clinician rather than the first record", () => {
    const clinicians = [clinician(), clinician({ clinicianId: "DR-002" })];
    expect(findClinicianById(clinicians, "DR-002")).toBe(clinicians[1]);
    expect(findClinicianById(clinicians, "DR-003")).toBeNull();
  });
});
