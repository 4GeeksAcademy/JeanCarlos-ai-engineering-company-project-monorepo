import {
  filterAppointmentsByStatus, filterClaims, groupClaimsBy,
  sortAppointmentsByDate, sortClaimsById,
} from "../../src/utils/collections";
import { appointment, claim } from "./fixtures";

describe("collection utilities", () => {
  const claims = [
    claim({ claimId: "CL-003", status: "pending" }),
    claim({ claimId: "CL-001" }),
    claim({ claimId: "CL-002", status: "pending", locationId: "clinic-b" }),
  ];

  test("combines claim filters and ignores undefined criteria", () => {
    expect(filterClaims(claims, {
      status: "pending", locationId: "clinic-a", payerName: undefined,
    })).toEqual([claims[0]]);
    expect(filterClaims(claims, {})).toEqual(claims);
    expect(filterClaims(claims, { payerName: "Missing Payer" })).toEqual([]);
    expect(filterClaims([], { status: "pending" })).toEqual([]);
  });

  test("filters by multiple appointment statuses and handles no selected status", () => {
    const appointments = [
      appointment(), appointment({ status: "no_show" }), appointment({ status: "cancelled" }),
    ];
    expect(filterAppointmentsByStatus(appointments, ["completed", "no_show"]))
      .toEqual(appointments.slice(0, 2));
    expect(filterAppointmentsByStatus(appointments, [])).toEqual([]);
    expect(filterAppointmentsByStatus([], ["no_show"])).toEqual([]);
  });

  test.each(["asc", "desc"] as const)("sorts claim IDs %s without mutating input", (direction) => {
    const original = [...claims];
    const expected = direction === "asc"
      ? ["CL-001", "CL-002", "CL-003"] : ["CL-003", "CL-002", "CL-001"];
    expect(sortClaimsById(claims, direction).map((item) => item.claimId)).toEqual(expected);
    expect(claims).toEqual(original);
  });

  test.each(["asc", "desc"] as const)("sorts appointment dates %s without mutating input", (direction) => {
    const appointments = [
      appointment({ scheduledDate: "2026-07-01" }),
      appointment({ scheduledDate: "2025-12-31" }),
      appointment({ scheduledDate: "2026-01-01" }),
    ];
    const original = [...appointments];
    const ascending = ["2025-12-31", "2026-01-01", "2026-07-01"];
    expect(sortAppointmentsByDate(appointments, direction).map((item) => item.scheduledDate))
      .toEqual(direction === "asc" ? ascending : ascending.reverse());
    expect(appointments).toEqual(original);
  });

  test("groups all matching claims while preserving their order", () => {
    expect(groupClaimsBy(claims, "status")).toEqual({
      pending: [claims[0], claims[2]], approved: [claims[1]],
    });
    expect(groupClaimsBy([], "locationId")).toEqual({});
    expect(sortClaimsById([], "asc")).toEqual([]);
    expect(sortAppointmentsByDate([], "asc")).toEqual([]);
  });
});
