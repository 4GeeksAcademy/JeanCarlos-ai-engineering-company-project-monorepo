import type { Appointment, Claim, Clinician, Location } from "../../src/types/models";

export function claim(overrides: Partial<Claim> = {}): Claim {
  return {
    claimId: "CL-001",
    patientId: "HC-ABC123",
    locationId: "clinic-a",
    serviceType: "primary_care",
    payerName: "Test Payer",
    payerId: "payer-a",
    submissionDate: "2026-06-15",
    claimAmount: 100,
    status: "approved",
    resubmitted: false,
    ...overrides,
  };
}

export function appointment(overrides: Partial<Appointment> = {}): Appointment {
  return {
    appointmentId: "AP-001",
    patientId: "HC-ABC123",
    locationId: "clinic-a",
    serviceType: "primary_care",
    scheduledDate: "2026-06-15",
    scheduledTime: "10:00",
    status: "completed",
    ...overrides,
  };
}

export function clinician(overrides: Partial<Clinician> = {}): Clinician {
  return {
    clinicianId: "DR-001",
    firstName: "Test",
    lastName: "Clinician",
    role: "physician",
    locationId: "clinic-a",
    licenceState: "FL",
    licenceExpiryDate: "2026-12-31",
    cmeHoursRequired: 20,
    cmeHoursLogged: 10,
    cmeYearStartDate: "2026-01-01",
    ...overrides,
  };
}

export const location: Location = {
  locationId: "clinic-a",
  name: "Test Clinic",
  city: "Test City",
  stateOrCountry: "FL",
  country: "US",
  phone: "000-000-0000",
  averageConsultationFee: {
    primary_care: 100.25,
    chronic_disease: 150,
    preventive: 80,
    specialist: 200,
    womens_health: 120,
    paediatric: 90,
    mental_health: 110,
  },
};
