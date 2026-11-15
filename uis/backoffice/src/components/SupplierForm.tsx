"use client";

import { useState, type FormEvent } from "react";
import { CATEGORIES, type Country, type SupplierCreate } from "@/types/supplier";

interface Props {
  busy: boolean;
  onSubmit: (supplier: SupplierCreate) => Promise<void>;
}

export default function SupplierForm({ busy, onSubmit }: Props) {
  const [country, setCountry] = useState<Country>("USA");
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const rawRate = data.get("monthly_rate");
    const monthly_rate = rawRate === null || rawRate === "" ? NaN : Number(rawRate);
    const categories = CATEGORIES.filter((category) =>
      data.getAll("categories").includes(category),
    );
    if (
      !String(data.get("name") ?? "").trim() ||
      !Number.isFinite(monthly_rate) ||
      monthly_rate <= 0 ||
      categories.length === 0
    ) {
      setError("Indica nombre, una tarifa mayor que cero y al menos una categoria.");
      return;
    }
    const agreement = data.get("compliance_agreement");
    const status = data.get("status");
    setError("");
    await onSubmit({
      name: String(data.get("name") ?? "").trim(),
      country,
      currency: country === "USA" ? "USD" : "GBP",
      categories,
      monthly_rate,
      status: status === "suspended" ? "suspended" : "active",
      compliance_agreement:
        agreement === "BAA" || agreement === "DPA" || agreement === "both"
          ? agreement
          : null,
      contract_renewal_date: String(data.get("contract_renewal_date") ?? "") || null,
      contact_email: String(data.get("contact_email") ?? "").trim() || null,
      notes: String(data.get("notes") ?? "").trim() || null,
    });
  }

  return (
    <section className="panel" aria-labelledby="create-title">
      <h2 id="create-title">Registrar proveedor</h2>
      <form onSubmit={submit}>
        <fieldset disabled={busy} className="form-grid">
          <legend className="sr-only">Datos del proveedor</legend>
          <label>
            Nombre *
            <input name="name" required />
          </label>
          <label>
            Pais *
            <select
              name="country"
              value={country}
              onChange={(event) =>
                setCountry(event.target.value === "UK" ? "UK" : "USA")
              }
            >
              <option value="USA">USA</option>
              <option value="UK">UK</option>
            </select>
          </label>
          <label>
            Tarifa mensual ({country === "USA" ? "USD" : "GBP"}) *
            <input name="monthly_rate" type="number" step="any" min="0" required />
          </label>
          <label>
            Estado *
            <select name="status" defaultValue="active">
              <option value="active">Activo</option>
              <option value="suspended">Suspendido</option>
            </select>
          </label>
          <fieldset className="categories">
            <legend>Categorias * (al menos una)</legend>
            {CATEGORIES.map((category) => (
              <label key={category} className="checkbox">
                <input type="checkbox" name="categories" value={category} />
                {category}
              </label>
            ))}
          </fieldset>
          <label>
            Acuerdo de cumplimiento
            <select name="compliance_agreement" defaultValue="">
              <option value="">Sin acuerdo registrado</option>
              <option value="BAA">BAA</option>
              <option value="DPA">DPA</option>
              <option value="both">both</option>
            </select>
          </label>
          <label>
            Renovacion del contrato
            <input name="contract_renewal_date" type="date" />
          </label>
          <label>
            Email de contacto
            <input name="contact_email" type="email" />
          </label>
          <label>
            Notas
            <textarea name="notes" rows={3} />
          </label>
          <p className="wide">
            Registra el acuerdo para clinical_software, it_infrastructure,
            patient_communication y billing_and_coding_software cuando
            corresponda. Su ausencia no impide el alta.
          </p>
          {error && <p role="alert" className="error wide">{error}</p>}
          <button type="submit">{busy ? "Guardando..." : "Crear proveedor"}</button>
        </fieldset>
      </form>
    </section>
  );
}
